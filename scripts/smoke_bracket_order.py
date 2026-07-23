#!/usr/bin/env python3
"""Live smoke test for stop-entry OCO (native Auto OCO) vs hybrid Position Brackets.

Two modes:

1) **Resting stop** (default) — far %-based stop-entry; cancels unless ``--keep``.
2) **``--verify-attach``** — market entry → hybrid SL/TP attach → assert protective
   stop+limit exist. Flattens by default; pass ``--keep`` to leave the position
   (and protective orders) open.

Examples (from repo root)::

  # Dry plan only
  .venv/bin/python scripts/smoke_bracket_order.py --account 1 --symbol MNQ

  # Place far stop + cancel
  .venv/bin/python scripts/smoke_bracket_order.py --account 1 --symbol MNQ --confirm

  # Prove post-fill SL/TP attach (opens 1 contract, then flattens)
  .venv/bin/python scripts/smoke_bracket_order.py --account 1 --symbol MNQ \\
      --confirm --verify-attach --force-hybrid

  # Same attach prove, leave position + SL/TP working and monitor until clean
  .venv/bin/python scripts/smoke_bracket_order.py --account 1 --symbol MNQ \\
      --confirm --verify-attach --force-hybrid --keep

Safety: refuses to place without ``--confirm``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

os.environ.setdefault("ENABLE_SIGNALR", "false")
os.environ.setdefault("DISABLE_DATABASE", "1")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Smoke-test stop-entry OCO / hybrid bracket placement + attach"
    )
    p.add_argument("--account", required=True, help="Account select index or id/name")
    p.add_argument("--symbol", default="MNQ", help="Symbol (default MNQ)")
    p.add_argument(
        "--side",
        default="BUY",
        choices=("BUY", "SELL", "buy", "sell"),
        help="Entry side",
    )
    p.add_argument("--qty", type=int, default=1, help="Contracts (default 1)")
    p.add_argument("--entry-pct", type=float, default=0.50, help="%% from last → entry stop")
    p.add_argument("--sl-pct", type=float, default=0.25, help="%% from entry/last → SL")
    p.add_argument("--tp-pct", type=float, default=0.25, help="%% from entry/last → TP")
    p.add_argument("--force-hybrid", action="store_true")
    p.add_argument("--force-native", action="store_true")
    p.add_argument("--confirm", action="store_true", help="Place live orders")
    p.add_argument(
        "--keep",
        action="store_true",
        help=(
            "Leave resting entry working (default mode), or with --verify-attach "
            "leave the filled position + protective SL/TP open (skip flatten)"
        ),
    )
    p.add_argument(
        "--verify-attach",
        action="store_true",
        help="Market entry + hybrid attach SL/TP + assert (flatten unless --keep)",
    )
    p.add_argument(
        "--wait-attach",
        type=float,
        default=0.0,
        help="After hybrid stop place, wait N seconds for attach (keeps process alive)",
    )
    p.add_argument("--no-discord", action="store_true")
    return p.parse_args()


def _last_from_quote(quote: Any) -> Optional[float]:
    if quote is None:
        return None
    if isinstance(quote, dict):
        if quote.get("error"):
            return None
        for key in ("last", "close", "mid", "bid", "ask"):
            val = quote.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    continue
        return None
    for attr in ("last", "close", "bid", "ask"):
        val = getattr(quote, attr, None)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


def _build_levels(
    *,
    last: float,
    side: str,
    entry_pct: float,
    sl_pct: float,
    tp_pct: float,
    filled_entry: Optional[float] = None,
) -> Tuple[float, float, float]:
    if entry_pct <= 0 or sl_pct <= 0 or tp_pct <= 0:
        raise SystemExit("entry-pct / sl-pct / tp-pct must be > 0")
    side_u = side.upper()
    base = float(filled_entry) if filled_entry is not None else last
    if side_u == "BUY":
        entry = last * (1.0 + entry_pct / 100.0) if filled_entry is None else base
        stop_loss = base * (1.0 - sl_pct / 100.0)
        take_profit = base * (1.0 + tp_pct / 100.0)
    else:
        entry = last * (1.0 - entry_pct / 100.0) if filled_entry is None else base
        stop_loss = base * (1.0 + sl_pct / 100.0)
        take_profit = base * (1.0 - tp_pct / 100.0)
    return entry, stop_loss, take_profit


async def _resolve_last(bot, symbol: str) -> float:
    quote = await bot.get_market_quote(symbol)
    last = _last_from_quote(quote)
    if last and last > 0:
        src = quote.get("source") if isinstance(quote, dict) else "quote"
        print(f"quote last={last:.4f} source={src}")
        return last
    try:
        bars = await bot.get_historical_data(symbol, timeframe="1m", limit=5)
        if bars:
            bar = bars[-1]
            close = bar.get("close") if isinstance(bar, dict) else None
            if close is not None:
                last = float(close)
                print(f"bars fallback last={last:.4f} (1m close)")
                return last
    except Exception as exc:
        print(f"history fallback failed: {exc}")
    raise SystemExit(f"Could not resolve last price for {symbol}: {quote!r}")


def _count_protective(
    orders: List[Dict], contract_id: str, *, exit_side: int
) -> Tuple[int, int, List[Dict]]:
    """Count stop/limit on contract with the exit side (0=BUY, 1=SELL)."""
    stops = 0
    limits = 0
    matched: List[Dict] = []
    for o in orders or []:
        cid = str(o.get("contractId") or o.get("contract_id") or "")
        if contract_id and cid and cid != str(contract_id):
            continue
        oside = o.get("side")
        try:
            oside_i = int(oside)
        except (TypeError, ValueError):
            oside_i = 0 if str(oside).upper() in ("BUY", "0", "LONG") else 1
        if oside_i != int(exit_side):
            continue
        otype = o.get("type")
        if otype in (4, "4", 3, "3") or "STOP" in str(otype).upper():
            stops += 1
            matched.append(o)
        elif otype in (1, "1") or "LIMIT" in str(otype).upper():
            limits += 1
            matched.append(o)
    return stops, limits, matched


async def _flatten(bot, account_id: str, symbol: str) -> None:
    positions = await bot.get_open_positions(account_id=account_id)
    for pos in positions or []:
        ps = str(pos.get("symbol") or "").upper()
        if not bot._position_symbol_matches(ps, symbol) and symbol not in str(
            pos.get("contractId") or ""
        ).upper():
            continue
        pid = pos.get("id") or pos.get("position_id")
        if pid:
            print(f"flatten position {pid}")
            print(await bot.close_position(str(pid), account_id=account_id))
    orders = await bot.get_open_orders(account_id=account_id)
    try:
        contract_id = bot._get_contract_id(symbol)
    except Exception:
        contract_id = None
    for o in orders or []:
        cid = str(o.get("contractId") or "")
        if contract_id and cid and cid != str(contract_id):
            continue
        oid = o.get("id") or o.get("orderId")
        if oid:
            print(f"cancel leftover {oid}")
            print(await bot.cancel_order(str(oid), account_id=account_id))


async def _run_verify_attach(bot, args, acct, symbol, side, last, tick) -> int:
    """Market open → register hybrid pending → attach → assert SL+TP → flatten."""
    account_id = str(acct.get("id"))
    _, sl_raw, tp_raw = _build_levels(
        last=last,
        side=side,
        entry_pct=args.entry_pct,
        sl_pct=args.sl_pct,
        tp_pct=args.tp_pct,
        filled_entry=last,
    )
    stop_loss = bot._round_to_tick_size(sl_raw, tick)
    take_profit = bot._round_to_tick_size(tp_raw, tick)
    print("--- verify-attach plan ---")
    print(f"market {side} {args.qty} {symbol} @ ~{last:.2f}")
    print(f"then attach SL={stop_loss:.4f} TP={take_profit:.4f}")

    if not args.confirm:
        print("DRY_RUN_OK (pass --confirm to open+attach)")
        return 0

    print("--- market entry ---")
    mkt = await bot.place_market_order(
        symbol=symbol,
        side=side,
        quantity=int(args.qty),
        account_id=account_id,
        strategy_name="smoke_hyb_attach",
    )
    print(f"market={mkt}")
    order_id = None
    if isinstance(mkt, dict):
        order_id = mkt.get("orderId") or mkt.get("order_id") or mkt.get("id")
    if not order_id or (isinstance(mkt, dict) and mkt.get("error")):
        print(f"MARKET_FAIL {mkt}")
        return 2
    order_id = str(order_id)

    # Wait for position
    position_id = None
    for i in range(20):
        pending_probe = {
            "account_id": account_id,
            "symbol": symbol,
            "side": side,
        }
        position_id = await bot._find_hybrid_position_id(pending_probe)
        if position_id:
            break
        await asyncio.sleep(0.5)
    if not position_id:
        print("POSITION_FAIL after market entry")
        await _flatten(bot, account_id, symbol)
        return 3
    print(f"position_id={position_id}")

    bot.register_hybrid_pending_bracket(
        order_id=order_id,
        symbol=symbol,
        side=side,
        quantity=int(args.qty),
        stop_loss_price=float(stop_loss),
        take_profit_price=float(take_profit),
        account_id=account_id,
        strategy_name="smoke_hyb_attach",
        entry_price=float(last),
    )

    print("--- attach ---")
    attach = await bot.try_attach_hybrid_brackets_on_fill(
        order_id, reason="smoke_verify_attach", order_status=2
    )
    print(f"attach={attach}")
    if not attach.get("attached"):
        print("ATTACH_FAIL")
        if not args.keep:
            await _flatten(bot, account_id, symbol)
        return 4

    await asyncio.sleep(1.0)
    orders = await bot.get_open_orders(account_id=account_id)
    try:
        contract_id = bot._get_contract_id(symbol)
    except Exception:
        contract_id = None
    exit_side = 1 if side.upper() in ("BUY", "LONG") else 0  # protective exit
    stops, limits, matched = _count_protective(
        orders or [], str(contract_id or ""), exit_side=exit_side
    )
    print(
        f"protective exit_side={exit_side} stops={stops} limits={limits} "
        f"matched={len(matched)}"
    )
    for o in matched:
        print(
            f"  order id={o.get('id')} type={o.get('type')} side={o.get('side')} "
            f"stop={o.get('stopPrice')} limit={o.get('limitPrice')}"
        )

    if stops < 1 or limits < 1:
        print("ATTACH_ASSERT_FAIL need >=1 stop and >=1 limit protective order")
        if not args.keep:
            await _flatten(bot, account_id, symbol)
        return 5

    if args.keep:
        print("--- keep ---")
        print(
            f"KEEPING position {position_id} with protective SL/TP "
            f"(sl={stop_loss:.4f} tp={take_profit:.4f})"
        )
        print(
            "Monitoring price-OCO + orphan sweeper until position flat + no TB-hyb-* "
            "(Ctrl-C to exit early)…"
        )
        bot._ensure_hybrid_orphan_sweeper()
        hub = getattr(bot, "user_hub_manager", None)
        hub_ok = bool(hub and getattr(hub, "is_connected", lambda: False)())
        print(
            f"user_hub_connected={hub_ok} "
            f"price_oco_poll={os.getenv('HYBRID_PRICE_OCO_POLL_S', '0.05')}s "
            f"(peer cancel fires on TP/SL quote touch — does not wait for Order/search)"
        )
        if not hub_ok:
            try:
                if hub and acct.get("id"):
                    started = await hub.start(account_id=acct.get("id"))
                    print(f"user_hub_start={started}")
                    if started:
                        bot._setup_event_driven_cache_invalidation(str(acct.get("id")))
            except Exception as exc:
                print(f"user_hub_start_err={exc}")
        try:
            idle_rounds = 0
            while True:
                await asyncio.sleep(0.5)
                try:
                    sweep = await bot.sweep_hybrid_orphan_orders(account_id=account_id)
                except Exception as exc:
                    print(f"sweep_err={exc}")
                    sweep = {}
                if sweep.get("cancelled"):
                    print(f"sweeper cancelled orphans: {sweep.get('cancelled')}")
                still_pos = await bot._find_hybrid_position_id(
                    {"account_id": account_id, "symbol": symbol, "side": side}
                )
                orders = await bot.get_open_orders(account_id=account_id)
                hyb = [
                    o
                    for o in (orders or [])
                    if bot.is_hybrid_protective_tag(o.get("customTag") or o.get("tag"))
                ]
                print(
                    f"  watch pos={still_pos or '(flat)'} hybrid_orders={len(hyb)} "
                    f"oco_legs={len(getattr(bot, '_hybrid_oco_legs', {}) or {})}"
                )
                if not still_pos and not hyb:
                    idle_rounds += 1
                    if idle_rounds >= 2:
                        print("CLEAN — position flat and no hybrid orphans")
                        break
                else:
                    idle_rounds = 0
        except KeyboardInterrupt:
            print("interrupted — leaving broker state as-is")
        print("ATTACH_SMOKE_OK")
        return 0

    print("--- flatten ---")
    await _flatten(bot, account_id, symbol)
    print("ATTACH_SMOKE_OK")
    return 0


async def _main() -> int:
    args = _parse_args()
    if args.force_hybrid and args.force_native:
        raise SystemExit("Use only one of --force-hybrid / --force-native")
    if args.force_hybrid:
        os.environ["TOPSTEPX_BRACKET_MODE"] = "position"
    elif args.force_native:
        os.environ["TOPSTEPX_BRACKET_MODE"] = "auto_oco"
    if args.no_discord:
        os.environ.pop("DISCORD_WEBHOOK_URL", None)

    from core.logging_setup import configure_logging
    from trading_bot import TopStepXTradingBot

    configure_logging()
    bot = TopStepXTradingBot()

    try:
        ok = await bot.authenticate()
        if not ok:
            print("AUTH_FAIL")
            return 1
        switched = await bot.switch_account(str(args.account))
        if not switched:
            print(f"ACCOUNT_FAIL {args.account}")
            return 1

        acct = bot.selected_account or {}
        print(
            f"account={acct.get('name')} id={acct.get('id')} "
            f"balance={acct.get('balance')}"
        )
        contracts = await bot.get_available_contracts(use_cache=True)
        if not contracts:
            print("CONTRACTS_FAIL empty")
            return 1
        print(f"contracts_cached={len(contracts)}")

        symbol = args.symbol.upper()
        side = args.side.upper()
        last = await _resolve_last(bot, symbol)
        tick = await bot._get_tick_size(symbol)

        if args.verify_attach:
            return await _run_verify_attach(bot, args, acct, symbol, side, last, tick)

        entry_raw, sl_raw, tp_raw = _build_levels(
            last=last,
            side=side,
            entry_pct=args.entry_pct,
            sl_pct=args.sl_pct,
            tp_pct=args.tp_pct,
        )
        entry = bot._round_to_tick_size(entry_raw, tick)
        stop_loss = bot._round_to_tick_size(sl_raw, tick)
        take_profit = bot._round_to_tick_size(tp_raw, tick)

        print("--- plan ---")
        print(f"symbol={symbol} side={side} qty={args.qty} tick={tick}")
        print(f"last={last:.4f}")
        print(
            f"entry={entry:.4f} ({args.entry_pct}% from last) "
            f"sl={stop_loss:.4f} ({args.sl_pct}% from entry) "
            f"tp={take_profit:.4f} ({args.tp_pct}% from entry)"
        )
        mode_env = os.getenv("TOPSTEPX_BRACKET_MODE") or "(unset → native first)"
        print(f"TOPSTEPX_BRACKET_MODE={mode_env}")
        print(
            f"prefer_hybrid={bot._should_prefer_hybrid_brackets()} "
            f"confirm={args.confirm} keep={args.keep} wait_attach={args.wait_attach}"
        )

        if not args.confirm:
            print("DRY_RUN_OK (pass --confirm to place)")
            return 0

        print("--- place ---")
        result: Dict[str, Any] = await bot.place_oco_bracket_with_stop_entry(
            symbol=symbol,
            side=side,
            quantity=int(args.qty),
            entry_price=float(entry),
            stop_loss_price=float(stop_loss),
            take_profit_price=float(take_profit),
            account_id=str(acct.get("id")),
            strategy_name="smoke_bracket_order",
        )
        print(f"result={result}")

        success = bool(
            isinstance(result, dict)
            and result.get("success")
            and result.get("orderId")
            and "error" not in result
        )
        method = (result or {}).get("method") if isinstance(result, dict) else None
        order_id = (result or {}).get("orderId") if isinstance(result, dict) else None

        if not success:
            print(f"PLACE_FAIL method={method} err={(result or {}).get('error')}")
            return 2

        path = "hybrid" if str(method or "").startswith("hybrid") else "native_or_adapter"
        print(f"PLACE_OK path={path} method={method} orderId={order_id}")
        pending = (getattr(bot, "_hybrid_pending_brackets", None) or {}).get(str(order_id))
        if pending:
            print(f"hybrid_pending=True (monitor armed for attach)")

        if args.wait_attach and order_id:
            print(f"--- wait-attach up to {args.wait_attach}s ---")
            deadline = asyncio.get_event_loop().time() + float(args.wait_attach)
            while asyncio.get_event_loop().time() < deadline:
                pend = (getattr(bot, "_hybrid_pending_brackets", None) or {}).get(str(order_id))
                if not pend:
                    print("WAIT_ATTACH_DONE (pending cleared)")
                    break
                await asyncio.sleep(1.0)
            else:
                print("WAIT_ATTACH_TIMEOUT (entry may not have filled)")

        if not args.keep and order_id and not args.wait_attach:
            print("--- cancel ---")
            cancel = await bot.cancel_order(str(order_id), account_id=str(acct.get("id")))
            print(f"cancel={cancel}")
            if isinstance(cancel, dict) and cancel.get("error"):
                print("CANCEL_WARN (order may still be working — check GUI)")
            else:
                print("CANCEL_OK")

        print("SMOKE_OK")
        return 0
    finally:
        try:
            if getattr(bot, "auth_manager", None):
                await bot.auth_manager.close()
        except Exception:
            pass
        try:
            if getattr(bot, "discord_notifier", None):
                await bot.discord_notifier.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
