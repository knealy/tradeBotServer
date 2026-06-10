"""Smoke tests for morning_range_reversion (sieve + wiring)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest


def test_morning_range_toml_per_symbol_tp_mult_overrides():
    """Per-symbol ``tp_mult`` overrides must be honoured. The MNQ override
    (2026-05-29 walk-forward), MES override (2026-05-29 MES-only sweep), AND
    the MGC override (2026-06-04 R26 introduced 1.8 → 2026-06-05 R27 bumped
    to 1.85) are asserted explicitly so a future TOML edit that removes them
    is caught.

    2026-06-05 R27 update: MGC ``tp_mult`` 1.8 → 1.85. Fresh-cache R10
    sweep showed tp=1.85 is the local optimum at cap=29 / sl_mult=3.30
    (RF 8.59 vs 1.80 RF 8.10 / 1.75 RF 7.62 / 1.90 RF 8.59 at cap=30 but
    crashes RF at cap=29). The R27 stack (cap 29 + slmult 3.30 + tp 1.85)
    is a clean cross-window Pareto improvement.
    """
    from core.strategy_config import load_strategy_config

    cfg = load_strategy_config("morning_range_reversion")
    assert float(cfg.symbol_override("MES", "signal.tp_mult", default=1.0)) == pytest.approx(0.7)
    assert float(cfg.symbol_override("MNQ", "signal.tp_mult", default=1.0)) == pytest.approx(1.25)
    # 2026-06-05 R27: MGC tp_mult bumped 1.8 → 1.85.
    assert float(cfg.symbol_override("MGC", "signal.tp_mult", default=-1.0)) == pytest.approx(1.85)


def test_sl_max_pts_and_floor_resolution_semantics(monkeypatch):
    """``signal.sl_max_pts`` and ``signal.sl_min_pts`` propagate through the
    StrategyConfig precedence chain (env > TOML > default) and per-symbol
    override path; the strategy's helpers return the effective values.

    Note: ``MorningRangeReversionStrategy.__init__`` re-loads its own cfg via
    ``load_strategy_config`` rather than using the cfg argument, so this test
    operates at the cfg-resolution layer (which is what ``_sl_max_pts`` /
    ``_sl_min_pts`` consult under the hood).
    """
    from core.strategy_config import load_strategy_config

    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_SL_MAX_PTS", "30")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_SL_MIN_PTS", "0")

    cfg = load_strategy_config("morning_range_reversion")
    # cfg-layer assertions (per-symbol overrides + root fallback)
    # MNQ has no sl_max_pts override → falls back to root (env=30).
    assert float(cfg.symbol_override("MNQ", "signal.sl_max_pts", default=0.0, hint=float)) == pytest.approx(30.0)
    assert float(cfg.get_float("signal.sl_max_pts", 0.0)) == pytest.approx(30.0)
    # sl_min_pts root remains 0 unless overridden
    assert float(cfg.get_float("signal.sl_min_pts", 0.0)) == pytest.approx(0.0)

    # Sanity floor lookup via env override.
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_SL_MIN_PTS", "8")
    cfg2 = load_strategy_config("morning_range_reversion")
    assert float(cfg2.get_float("signal.sl_min_pts", 0.0)) == pytest.approx(8.0)


def test_sl_max_pct_of_range_dynamic_cap_resolution():
    """``signal.sl_max_pct_of_range`` is a dynamic per-day cap expressed as a
    fraction of the anchor range width — verify it resolves through the cfg
    layer for per-symbol overrides and root fallback."""
    from core.strategy_config import load_strategy_config

    cfg = load_strategy_config("morning_range_reversion")
    # No root override yet → 0.0 default.
    assert float(cfg.get_float("signal.sl_max_pct_of_range", 0.0)) == pytest.approx(0.0)


def test_entry_window_time_parsing():
    """``_parse_et_time`` accepts HH:MM and HH:MM:SS, tolerates quoting."""
    from datetime import time as dt_time
    from strategies.morning_range_reversion_strategy import (
        MorningRangeReversionStrategy as MRR,
    )
    assert MRR._parse_et_time("09:30") == dt_time(9, 30)
    assert MRR._parse_et_time("'14:00'") == dt_time(14, 0)
    assert MRR._parse_et_time("10:15:30") == dt_time(10, 15, 30)
    assert MRR._parse_et_time(None) is None
    assert MRR._parse_et_time("") is None
    assert MRR._parse_et_time("not-a-time") is None


def test_consec_loss_breaker_reads_engine_trades(monkeypatch):
    """The cross-session consec-loss breaker inspects
    ``self._replay_engine.trades`` (read-only) and trips after N
    contiguous losers, releasing after ``loss_streak_cooldown_sessions``
    calendar days.  No strategy state writes — safe to call on every
    bar without altering replay determinism.

    Uses MNQ (no per-symbol TOML breaker override committed) so env-var
    settings reach the breaker. MGC / MES carry committed TOML defaults
    (mcl=2, cd=10) which would shadow the test's env overrides.
    """
    from datetime import date as _date, datetime as _datetime
    from types import SimpleNamespace
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_CONSECUTIVE_LOSSES", "3")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_LOSS_STREAK_COOLDOWN_SESSIONS", "5")
    strat = MorningRangeReversionStrategy(_MockBot([]), None)
    assert strat._max_consecutive_losses("MNQ") == 3
    assert strat._loss_streak_cooldown_sessions("MNQ") == 5

    def mk(symbol, pnl, exit_d):
        return SimpleNamespace(symbol=symbol, pnl=pnl, exit_time=_datetime(exit_d.year, exit_d.month, exit_d.day, 14, 30))

    strat._replay_engine = SimpleNamespace(trades=[
        mk("MNQ", +100, _date(2025, 11, 25)),  # winner
        mk("MNQ",  -50, _date(2025, 11, 26)),  # loss 1
        mk("MNQ",  -50, _date(2025, 11, 27)),  # loss 2
        mk("MNQ",  -50, _date(2025, 11, 28)),  # loss 3 → TRIP
    ])

    s = strat._consec_loss_breaker_status("MNQ", _date(2025, 11, 29))
    assert s["blocked"] is True and s["streak"] == 3
    assert s["trip_session_date"] == _date(2025, 11, 28)
    # 5-day cooldown from Nov 28 → Dec 3 inclusive.
    assert strat._consec_loss_breaker_status("MNQ", _date(2025, 12, 2))["blocked"] is True
    assert strat._consec_loss_breaker_status("MNQ", _date(2025, 12, 3))["blocked"] is False
    # Cross-symbol isolation: ZZZ has no trades and no TOML override.
    assert strat._consec_loss_breaker_status("ZZZ", _date(2025, 12, 1))["blocked"] is False
    # A winner *after* the tripping loss resets the streak.
    strat._replay_engine.trades.append(mk("MNQ", +200, _date(2025, 11, 29)))
    s = strat._consec_loss_breaker_status("MNQ", _date(2025, 11, 30))
    assert s["blocked"] is False and s["streak"] == 0


def test_consec_loss_breaker_magnitude_filter(monkeypatch):
    """``rolling_pnl_loss_threshold_dollars`` ANDs with the count check —
    when the streak's cumulative PnL is shallower than the threshold,
    the breaker doesn't trip (the streak is treated as normal-market
    noise rather than regime change)."""
    from datetime import date as _date, datetime as _datetime
    from types import SimpleNamespace
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_CONSECUTIVE_LOSSES", "2")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_LOSS_STREAK_COOLDOWN_SESSIONS", "10")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_ROLLING_PNL_LOSS_THRESHOLD_DOLLARS", "500")
    strat = MorningRangeReversionStrategy(_MockBot([]), None)
    assert strat._rolling_loss_threshold_dollars("MNQ") == 500.0

    def mk(symbol, pnl, exit_d):
        return SimpleNamespace(symbol=symbol, pnl=pnl, exit_time=_datetime(exit_d.year, exit_d.month, exit_d.day, 14, 30))

    # ── Case 1: shallow 2-loss streak (-100 + -150 = -250 > -500) → NOT
    #            blocked.  Magnitude filter suppresses normal-market noise.
    strat._replay_engine = SimpleNamespace(trades=[
        mk("MNQ", +50, _date(2025, 12, 1)),
        mk("MNQ", -100, _date(2025, 12, 2)),
        mk("MNQ", -150, _date(2025, 12, 3)),
    ])
    s = strat._consec_loss_breaker_status("MNQ", _date(2025, 12, 4))
    assert s["blocked"] is False
    assert s["reason"] == "shallow_streak_skipped"
    assert s["streak"] == 2

    # ── Case 2: deep 2-loss streak (-300 + -400 = -700 < -500) → BLOCKED.
    #            Streak deep enough to indicate regime — breaker fires.
    strat._replay_engine.trades = [
        mk("MNQ", +50, _date(2025, 12, 1)),
        mk("MNQ", -300, _date(2025, 12, 2)),
        mk("MNQ", -400, _date(2025, 12, 3)),
    ]
    s = strat._consec_loss_breaker_status("MNQ", _date(2025, 12, 4))
    assert s["blocked"] is True
    assert s["streak_pnl"] == -700.0


def test_consec_loss_breaker_mgc_default_from_toml():
    """Committed TOML defaults: MGC + MES have mcl=2, cd=10.  This is the
    Round-19 regime-detection layer that protects against trend-cluster
    drawdowns (e.g. late-2025 MGC bleed).  MNQ has no breaker."""
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    strat = MorningRangeReversionStrategy(_MockBot([]), None)
    assert strat._max_consecutive_losses("MGC") == 2
    assert strat._loss_streak_cooldown_sessions("MGC") == 10
    assert strat._max_consecutive_losses("MES") == 2
    assert strat._loss_streak_cooldown_sessions("MES") == 10
    # MNQ stays off so its broader-stop / fixed-pts geometry isn't
    # affected by the breaker (which materially hurt MNQ ret in
    # universal-breaker sweeps, -60% on 9m).
    assert strat._max_consecutive_losses("MNQ") == 0
    assert strat._loss_streak_cooldown_sessions("MNQ") == 0


def test_per_symbol_position_size_committed_defaults():
    """``_position_size(symbol)`` reads ``[symbols.<SYM>.risk].position_size`` first
    then falls back to root ``[risk].position_size`` via ``StrategyConfig.symbol_override``'s
    own fallback chain.

    Round-24 committed config: MNQ doubled to 4 contracts (best Pareto
    improvement in the 7-variant weighting sweep — +18-22% return on every
    window with same-or-better DD and RF).  MES and MGC inherit root
    ``[risk].position_size = 2``.
    """
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    strat = MorningRangeReversionStrategy(_MockBot([]), _live_cfg())
    root_size = int(strat._cfg.get("risk.position_size", default=1))
    assert root_size == 2, f"root position_size drift: {root_size}"
    # Committed: MNQ 2× (4 contracts), MES/MGC inherit root (2).
    assert strat._position_size("MNQ") == 4
    assert strat._position_size("MES") == root_size
    assert strat._position_size("MGC") == root_size

    # Mutating only one symbol must not bleed into the others.
    strat._cfg._data.setdefault("symbols", {}).setdefault("MGC", {}).setdefault("risk", {})["position_size"] = 3
    assert strat._position_size("MGC") == 3
    assert strat._position_size("MNQ") == 4  # unchanged
    assert strat._position_size("MES") == root_size  # unchanged


def test_per_symbol_skip_weekdays_committed_defaults():
    """Per-symbol weekday filters (committed as of 2026-06-04 R26):

    * MGC: ``["Fri"]`` only — 2026-06-04 R26 dropped the previous ``Wed`` skip
      after the engine-fix audit. The pre-fix evidence ("MGC Wed has 40% loss
      rate") was an H-B gap-through-phantom artefact; on the corrected engine
      R3 sweep showed un-skipping Wed adds 17 MGC trades and lifts RF from
      6.45 → 8.49 with DD unchanged. The Fri-skip remains valid on the
      corrected engine (no R5 trial overturned it).
    * MNQ: ``["Mon", "Thu", "Fri"]`` — MNQ Mon and Thu have 53-57% loss
      rates across all windows. R4/R5 sweeps confirmed this set is still
      optimal on the corrected engine (skip-none lifts ret but drops RF
      to 8.16 vs the current 9.71).
    * MES: inherits root ``["Fri"]`` only — MES stanzas remain dormant in
      committed symbols list; this assertion still pins the inheritance
      logic so a future MES re-enable doesn't reintroduce the bug.

    This test pins the precedence: per-symbol TOML overrides take priority
    over the root ``signal.skip_weekdays``.
    """
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    strat = MorningRangeReversionStrategy(_MockBot([]), None)
    Mon, Tue, Wed, Thu, Fri = 0, 1, 2, 3, 4
    # 2026-06-04 R26: MGC Wed-skip removed; only Fri remains.
    assert strat._skip_weekdays("MGC") == frozenset({Fri})
    assert strat._skip_weekdays("MNQ") == frozenset({Mon, Thu, Fri})
    # MES has no per-symbol override; inherits root [Fri].
    assert strat._skip_weekdays("MES") == frozenset({Fri})


def test_efficiency_ratio_computation():
    """KER over a perfect trend is 1.0; over a perfect oscillation 0.0."""
    from datetime import date as _date, datetime as _datetime
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    strat = MorningRangeReversionStrategy(_MockBot([]), None)
    # Helper: build a bar with a given timestamp (UTC iso) and close.
    def mk(d, c):
        # Convert ET-date d to a UTC iso ~mid-session (avoids DST edge cases).
        ts = _datetime(d.year, d.month, d.day, 18, 0)  # ~13:00 ET in winter
        return {"timestamp": ts.isoformat() + "Z", "close": c}

    # Perfect monotonic uptrend: closes [100, 101, 102, 103, 104, 105].
    bars_trend = [mk(_date(2026, 1, 1 + i), 100 + i) for i in range(6)]
    er = strat._compute_efficiency_ratio(bars_trend, _date(2026, 1, 7), lookback_days=5)
    assert er is not None and er > 0.99

    # Perfect oscillation: closes [100, 110, 100, 110, 100, 110] → net 10,
    # gross 50, ER = 0.20.
    bars_osc = [mk(_date(2026, 1, 1 + i), 100 + (10 if i % 2 else 0)) for i in range(6)]
    er2 = strat._compute_efficiency_ratio(bars_osc, _date(2026, 1, 7), lookback_days=5)
    assert er2 is not None and 0.15 < er2 < 0.25

    # Insufficient history → None.
    er3 = strat._compute_efficiency_ratio(bars_trend[:2], _date(2026, 1, 7), lookback_days=5)
    assert er3 is None


def test_consec_loss_breaker_disabled_when_knob_zero():
    """When ``max_consecutive_losses=0`` the breaker is fully off — no
    engine reads, no state writes, deterministic by construction.  Uses
    MNQ since it carries no TOML breaker override (default 0)."""
    from datetime import date as _date
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    strat = MorningRangeReversionStrategy(_MockBot([]), None)
    assert strat._max_consecutive_losses("MNQ") == 0
    s = strat._consec_loss_breaker_status("MNQ", _date(2025, 12, 1))
    assert s == {"blocked": False, "streak": 0, "reason": "ok"}
    # record_trade_outcome is a no-op stub (legacy live hook).
    strat.record_trade_outcome("MNQ", -100.0)


def test_morning_range_toml_root_has_sl_max_pts_safety_cap():
    """Root TOML must ship with the 2026-05-29 walk-forward cap=30 safety
    bound so a future operator who clears symbol overrides still benefits
    from the worst-case-tail trim.  ``sl_min_pts`` stays at 0 (no floor)."""
    from core.strategy_config import load_strategy_config

    cfg = load_strategy_config("morning_range_reversion")
    assert float(cfg.get_float("signal.sl_max_pts", 0.0)) == pytest.approx(30.0)
    assert float(cfg.get_float("signal.sl_min_pts", 0.0)) == pytest.approx(0.0)


def test_morning_range_toml_per_symbol_sl_mult_overrides():
    """Per-symbol ``sl_mult`` overrides — risk-normalized stop geometry for
    smaller-tick contracts so range-anchored SLs apply correctly on each:

    * MES: ``sl_mult=3.0`` (2026-05-29 MES risk-normalization).
    * MGC: ``sl_mult=3.30`` (**2026-06-05 R27 update**, was 3.25). R10 fresh-cache
      sweep at cap=29 showed 3.30 is the local optimum (RF 8.28 vs 3.25 RF
      8.10 / 3.20 RF 8.24) and the user-requested geometry improvement
      (worst MGC loss $612 → $592) ships with this commit.
    * MES + MGC: explicit ``sl_fixed_pts=0`` so range-anchored ``sl_mult``
      governs.
    * MNQ: keeps ``sl_fixed_pts=50`` (not range-anchored). The 2026-05-29
      dynamic ``sl_mult`` sweep tied baseline on 9m but regressed on 3m
      (108→49%) / 6m (71→6%); R4 + R10 sweeps confirmed MNQ overrides are
      still engine-robust post-fix. See TOML inline note in [symbols.MNQ.signal].
    """
    from core.strategy_config import load_strategy_config

    cfg = load_strategy_config("morning_range_reversion")
    assert float(cfg.symbol_override("MES", "signal.sl_mult", default=1.0)) == pytest.approx(3.0)
    # 2026-06-05 R27: MGC sl_mult lifted 3.25 → 3.30.
    assert float(cfg.symbol_override("MGC", "signal.sl_mult", default=1.0)) == pytest.approx(3.30)
    # MES + MGC explicitly zero sl_fixed_pts so range-anchored sl_mult wins.
    assert float(cfg.symbol_override("MES", "signal.sl_fixed_pts", default=-1.0)) == pytest.approx(0.0)
    assert float(cfg.symbol_override("MGC", "signal.sl_fixed_pts", default=-1.0)) == pytest.approx(0.0)
    # MNQ keeps slfix=50 — the dynamic sl_mult sweep (2026-05-29, 9m) only
    # tied baseline on the long window AND regressed on 3m / 6m (ret 108%
    # → 49% on 3m, 71% → 6% on 6m). R4 + R10 sweep confirmed MNQ slfix=50
    # still holds on the corrected engine. See TOML inline note.
    assert float(cfg.symbol_override("MNQ", "signal.sl_fixed_pts", default=-1.0)) == pytest.approx(50.0)
    # 2026-06-05 R27: MGC sl_max_pts tightened 30 → 29 (worst-case tail trim
    # validated on fresh-cache truth — cuts worst MGC loss by $20 / -3.3 %
    # AND keeps RF / DD strictly better than baseline).
    assert float(cfg.symbol_override("MGC", "signal.sl_max_pts", default=-1.0)) == pytest.approx(29.0)
    # 2026-06-05 R28 (material PnL lift): MGC max_range_width_points
    # tightened 65 → 46.  Pareto improvement across 3m/6m/9m:
    #   9m: +17.7% ret, +18% RF, +1.3pp WR (DD identical)
    #   6m: +20.6% ret, +21% RF, +2.0pp WR (DD identical)
    #   3m: +19.3% ret, +19% RF, +2.3pp WR
    # Skips the wide-range volatility-cluster sessions that produced the
    # user-reported worst MGC losers (2025-11-13 BUY -$526, 2025-11-18
    # SELL -$554).
    assert float(cfg.symbol_override("MGC", "signal.max_range_width_points", default=-1.0)) == pytest.approx(46.0)


def test_strategy_registered_in_manager():
    from strategies.strategy_manager import BUILTIN_STRATEGY_SPECS

    assert "morning_range_reversion" in BUILTIN_STRATEGY_SPECS
    module, cls, _d = BUILTIN_STRATEGY_SPECS["morning_range_reversion"]
    assert module == "strategies.morning_range_reversion_strategy"
    assert cls == "MorningRangeReversionStrategy"


def test_strategy_registered_in_backtest_executor():
    from core.backtest_executor import BacktestExecutor

    klass = BacktestExecutor()._get_strategy_class("morning_range_reversion")
    assert klass is not None
    assert klass.__name__ == "MorningRangeReversionStrategy"


def test_live_readiness_committed_meta_flags():
    """2026-06-09 live-readiness audit pinning test.

    Pins the meta-level toggles that gate live behaviour:
      • ``meta.enabled = true``           — strategy is the production powerhouse
      • ``meta.symbols`` ⊇ ['MNQ', 'MGC'] — live deploy rotation (MES dropped R25)
      • ``meta.live_breaker_enabled = true`` — the per-strategy consec-loss
        breaker subscribes to ``EventType.TRADE_CLOSED`` so live fills feed
        the in-memory trade history.  Required for the breaker to do
        anything meaningful in live mode (no-op in backtest).  Without this
        the strategy still runs but the breaker never trips.

    Catches any future TOML edit that accidentally flips one of these off.
    """
    from core.strategy_config import load_strategy_config

    cfg = load_strategy_config("morning_range_reversion")
    assert cfg.get_bool("meta.enabled") is True, "MRR is the powerhouse, must stay enabled"
    syms = cfg.get("meta.symbols") or []
    assert "MNQ" in syms and "MGC" in syms, (
        f"meta.symbols must include MNQ + MGC for live rotation; got {syms}"
    )
    assert cfg.get_bool("meta.live_breaker_enabled") is True, (
        "meta.live_breaker_enabled must be true; otherwise live consec-loss "
        "breaker is a no-op (no TRADE_CLOSED subscription)."
    )


def test_live_readiness_position_size_aligned_with_wrapper_script():
    """The wrapper script ``scripts/run_morning_reversion.sh`` passes a
    ``--risk-config`` JSON with per-symbol ``max_quantity``.  If that
    value drifts from the TOML's ``position_size`` (root or per-symbol
    override), the executor silently throttles or over-sizes.

    The script's risk-config block (as of 2026-06-09 R28 commit):
        MNQ: max_quantity=4, cooldown=60, max_pending=2
        MES: max_quantity=2, cooldown=60, max_pending=2
        MGC: max_quantity=2, cooldown=60, max_pending=2

    TOML must match: root ``position_size = 2`` (the MES + MGC default),
    plus ``[symbols.MNQ.risk] position_size = 4`` (R24 2× weighting).
    """
    from core.strategy_config import load_strategy_config

    cfg = load_strategy_config("morning_range_reversion")
    # MNQ override = 4 (R24)
    mnq_size = cfg.symbol_override("MNQ", "risk.position_size", default=None)
    assert int(mnq_size) == 4, (
        f"MNQ position_size must be 4 to match scripts/run_morning_reversion.sh "
        f"--risk-config MNQ.max_quantity=4; got {mnq_size}"
    )
    # MGC has no risk override (uses root default = 2)
    mgc_size = cfg.symbol_override("MGC", "risk.position_size", default=None)
    assert mgc_size is None or int(mgc_size) == 2, (
        f"MGC position_size must be 2 (root default) to match "
        f"scripts/run_morning_reversion.sh --risk-config MGC.max_quantity=2; "
        f"got {mgc_size}"
    )
    # Root default
    assert int(cfg.get_int("risk.position_size", 0)) == 2, (
        "Root [risk].position_size must be 2 (the MES + MGC default)"
    )


def test_live_readiness_flat_before_enforces_session_close():
    """``signal.flat_before`` must be set so the live ``manage_positions``
    path closes any open positions at session end.  Default 16:00 ET.

    A missing ``flat_before`` would leave positions open past 16:00 in
    live mode (potentially violating prop-firm same-session rules).
    """
    from core.strategy_config import load_strategy_config
    from datetime import time

    cfg = load_strategy_config("morning_range_reversion")
    fb = cfg.get_str("signal.flat_before", "")
    assert fb, "signal.flat_before MUST be set for live deploy"
    parts = fb.split(":")
    assert len(parts) == 2 and int(parts[0]) > 0, f"signal.flat_before malformed: {fb}"
    hh, mm = int(parts[0]), int(parts[1])
    assert 12 <= hh <= 17, (
        f"signal.flat_before={fb} ET is suspicious for a morning fade strategy. "
        f"Expected late-morning to mid-afternoon ET (12:00–17:00). "
        f"Half-day prop accounts may need earlier (e.g. 12:30)."
    )


def test_sieve_immediate_mode_does_not_churn_while_close_stays_outside():
    """Regression: consecutive closes beyond the range arm at most once until a close inside [L,H]."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    start = pd.Timestamp("2026-01-05 12:00", tz="UTC")
    idx = [start + pd.Timedelta(minutes=5 * i) for i in range(16)]
    rows = []
    for i in range(16):
        if i < 12:
            rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
        elif i == 12:
            # First close above H — immediate SHORT; stop tags 115 same bar → one loss trade
            rows.append({"open": 105, "high": 115, "low": 104, "close": 111})
        elif i in (13, 14):
            # Stay outside without re-entering the box — must not create new sieves
            rows.append({"open": 112, "high": 116, "low": 111, "close": 114})
        else:
            rows.append({"open": 112, "high": 112, "low": 105, "close": 106})
    df = pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])
    tr = sieve_simulate_from_ohlcv(df, require_reentry_close=False)
    assert len(tr) == 1
    assert tr[0].outcome == "loss"


def test_sieve_range_effectiveness_hours_blocks_late_sweep():
    """After range_end_open + N hours ET, no new fade from a sweep (default 4h → 12:00 ET cutoff)."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    start = pd.Timestamp("2026-01-05 12:00", tz="UTC")
    idx = [start + pd.Timedelta(minutes=5 * i) for i in range(62)]
    rows = []
    for i in range(62):
        if i < 12:
            rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
        elif i < 61:
            rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
        else:
            rows.append({"open": 105, "high": 115, "low": 104, "close": 111})
    df = pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])
    assert len(sieve_simulate_from_ohlcv(df, require_reentry_close=False)) == 0
    assert len(sieve_simulate_from_ohlcv(df, require_reentry_close=False, range_effectiveness_hours=0)) >= 1


def test_sieve_max_fades_per_session_blocks_second_arm_same_day():
    """After one fade + inside reset, a second arm is skipped when max_fades_per_session=1."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    start = pd.Timestamp("2026-01-05 12:00", tz="UTC")
    idx = [start + pd.Timedelta(minutes=5 * i) for i in range(20)]
    rows = []
    for i in range(12):
        rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
    rows.append({"open": 105, "high": 115, "low": 104, "close": 111})
    rows.append({"open": 112, "high": 116, "low": 111, "close": 114})
    rows.append({"open": 112, "high": 112, "low": 105, "close": 106})
    rows.append({"open": 106, "high": 112, "low": 105, "close": 111})
    for _ in range(15, 19):
        rows.append({"open": 106, "high": 108, "low": 104, "close": 105})
    df = pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])
    tr0 = sieve_simulate_from_ohlcv(df, require_reentry_close=False)
    tr1 = sieve_simulate_from_ohlcv(df, require_reentry_close=False, max_fades_per_session=1)
    assert len(tr0) >= 2
    assert len(tr1) == 1


def test_sieve_high_sweep_short_win():
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    start = pd.Timestamp("2026-01-05 12:00", tz="UTC")
    idx = [start + pd.Timedelta(minutes=5 * i) for i in range(15)]
    rows = []
    for i in range(15):
        if i < 12:
            rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
        elif i == 12:
            rows.append({"open": 105, "high": 112, "low": 104, "close": 111})
        elif i == 13:
            rows.append({"open": 111, "high": 111, "low": 105, "close": 105})
        else:
            rows.append({"open": 105, "high": 108, "low": 104, "close": 107})
    df = pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])
    tr = sieve_simulate_from_ohlcv(df, require_reentry_close=True)
    assert len(tr) == 1
    assert tr[0].outcome == "win"
    assert tr[0].side == "SHORT"
    assert tr[0].sweep == "high"


def test_sieve_pre_range_overnight_bars_do_not_skip_morning_window():
    """Regression: first bars of the ET day before 07:00 must not finalize to idle."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    # Jan 5 2026: 05:00 UTC = midnight ET (EST); then 7am ET = 12:00 UTC same as other tests.
    t_midnight_et = pd.Timestamp("2026-01-05 05:00", tz="UTC")
    t_range0 = pd.Timestamp("2026-01-05 12:00", tz="UTC")
    idx = [t_midnight_et + pd.Timedelta(minutes=5 * i) for i in range(3)]
    idx += [t_range0 + pd.Timedelta(minutes=5 * j) for j in range(15)]
    rows = []
    for _ in range(3):
        rows.append({"open": 99, "high": 100, "low": 98, "close": 99})
    for j in range(15):
        if j < 12:
            rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
        elif j == 12:
            rows.append({"open": 105, "high": 112, "low": 104, "close": 111})
        elif j == 13:
            rows.append({"open": 111, "high": 111, "low": 105, "close": 105})
        else:
            rows.append({"open": 105, "high": 108, "low": 104, "close": 107})
    df = pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])
    tr = sieve_simulate_from_ohlcv(df, require_reentry_close=True)
    assert len(tr) == 1
    assert tr[0].outcome == "win"


def test_sieve_1m_path_tp_before_stop_same_5m_bar():
    """1m path hits target before stop on a 5m bar where both extremes touch."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    start = pd.Timestamp("2026-01-06 12:00", tz="UTC")
    idx = [start + pd.Timedelta(minutes=5 * i) for i in range(15)]
    rows = []
    for i in range(15):
        if i < 12:
            rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
        elif i == 12:
            rows.append({"open": 105, "high": 112, "low": 104, "close": 111})
        elif i == 13:
            rows.append({"open": 111, "high": 116, "low": 104, "close": 105})
        else:
            rows.append({"open": 105, "high": 106, "low": 105, "close": 105})
    df5 = pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])
    t5 = idx[13].tz_convert(None)
    # Five 1m bars covering the ambiguous 5m [t5, t5+5m)
    im = []
    for k in range(5):
        ts = t5 + pd.Timedelta(minutes=k)
        if k == 0:
            im.append((ts, {"open": 111, "high": 111, "low": 104, "close": 104}))
        elif k == 1:
            im.append((ts, {"open": 104, "high": 116, "low": 104, "close": 110}))
        else:
            im.append((ts, {"open": 110, "high": 110, "low": 109, "close": 109}))
    df1 = pd.DataFrame([x[1] for x in im], index=[x[0] for x in im])
    tr = sieve_simulate_from_ohlcv(
        df5,
        one_minute_df=df1,
        stop_before_target_same_bar=True,
        require_reentry_close=True,
    )
    assert len(tr) == 1
    assert tr[0].outcome == "win"


def test_sieve_conservative_same_bar_stop_wins():
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    start = pd.Timestamp("2026-01-06 12:00", tz="UTC")
    idx = [start + pd.Timedelta(minutes=5 * i) for i in range(15)]
    rows = []
    for i in range(15):
        if i < 12:
            rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
        elif i == 12:
            rows.append({"open": 105, "high": 112, "low": 104, "close": 111})
        elif i == 13:
            # re-entry bar: spikes both stop (115) and target (105) — conservative loss
            rows.append({"open": 111, "high": 116, "low": 104, "close": 105})
        else:
            rows.append({"open": 105, "high": 106, "low": 105, "close": 105})
    df = pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])
    tr = sieve_simulate_from_ohlcv(df, stop_before_target_same_bar=True, require_reentry_close=True)
    assert len(tr) == 1
    assert tr[0].outcome == "loss"


def test_sieve_tp_mult_changes_take_profit_target():
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    start = pd.Timestamp("2026-01-05 12:00", tz="UTC")
    idx = [start + pd.Timedelta(minutes=5 * i) for i in range(15)]
    rows = []
    for i in range(15):
        if i < 12:
            rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
        elif i == 12:
            rows.append({"open": 105, "high": 112, "low": 104, "close": 111})
        elif i == 13:
            rows.append({"open": 111, "high": 111, "low": 105, "close": 105})
        else:
            rows.append({"open": 105, "high": 108, "low": 104, "close": 107})
    df = pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])
    tr1 = sieve_simulate_from_ohlcv(df, require_reentry_close=True, tp_mult=1.0)
    tr07 = sieve_simulate_from_ohlcv(df, require_reentry_close=True, tp_mult=0.7)
    assert len(tr1) == 1 and len(tr07) == 1
    assert abs(tr1[0].target - tr07[0].target) > 0.25


def test_sieve_reentry_frac_blocks_shallow_close_inside_box():
    """BONGO §4.2: inner band [L+frac*W, H-frac*W] must match live analyze."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    start = pd.Timestamp("2026-01-05 12:00", tz="UTC")

    def make_df(reentry_close: float, n_bars: int):
        idx = [start + pd.Timedelta(minutes=5 * i) for i in range(n_bars)]
        rows = []
        for i in range(n_bars):
            if i < 12:
                rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
            elif i == 12:
                rows.append({"open": 105, "high": 112, "low": 104, "close": 111})
            elif i == 13:
                rows.append(
                    {"open": 111, "high": 112, "low": 100, "close": reentry_close}
                )
            else:
                rows.append({"open": 105, "high": 108, "low": 104, "close": 107})
        return pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])

    # H=110 L=100 W=10; frac=0.15 → inner [101.5, 108.5]. Close 101 is inside [L,H] but too shallow.
    # Stop after the shallow re-entry bar so a later "deep by accident" bar cannot arm.
    df_shallow = make_df(101.0, n_bars=14)
    assert len(sieve_simulate_from_ohlcv(df_shallow, require_reentry_close=True, reentry_frac=0.15)) == 0

    df_ok = make_df(102.0, n_bars=15)
    assert len(sieve_simulate_from_ohlcv(df_ok, require_reentry_close=True, reentry_frac=0.15)) == 1


class _MockBot:
    def __init__(self, bars):
        self.bars = bars
        self.selected_account = {"id": "test", "name": "TEST"}
        self._is_strategy_replay = True
        self._current_bar_timestamp = bars[-1]["timestamp"] if bars else None

    async def get_historical_data(self, symbol, timeframe=None, limit=None, **_):
        if limit:
            return self.bars[-limit:]
        return self.bars


def _utc(*args, **kwargs):
    return datetime(*args, tzinfo=timezone.utc, **kwargs)


def test_analyze_emits_short_immediate_on_first_close_outside_high(monkeypatch):
    """Immediate-fade path (legacy default): stop-entry on the sweep bar."""
    # The validated TOML default is now require_reentry_close=true. This test
    # pins the original immediate-fade behaviour, so opt in via env override.
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_REENTRY_CLOSE", "false")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REENTRY_THRESHOLD_POINTS", "0")
    # The fixture builds a 10-pt synthetic range; bypass the production
    # narrow/wide range filters so the strategy doesn't reject the test data
    # outright. Production TOML keeps these on (50 pt floor, 300 pt ceiling).
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MIN_RANGE_WIDTH_POINTS", "0")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_RANGE_WIDTH_POINTS", "0")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="morning_range_reversion",
        enabled=True,
        symbols=["MNQ"],
        max_positions=1,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=12,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )
    t0 = _utc(2026, 1, 7, 12, 0)
    bars = []
    for i in range(12):
        bars.append(
            {
                "timestamp": t0 + timedelta(minutes=5 * i),
                "open": 105,
                "high": 110,
                "low": 100,
                "close": 105,
                "volume": 1,
            }
        )
    bars.append(
        {
            "timestamp": t0 + timedelta(minutes=5 * 12),
            "open": 105,
            "high": 112,
            "low": 104,
            "close": 111,
            "volume": 1,
        }
    )
    bot = _MockBot(bars[:1])
    strat = MorningRangeReversionStrategy(bot, cfg)
    sig = None
    k_at = None
    for k in range(1, len(bars) + 1):
        bot.bars = bars[:k]
        bot._current_bar_timestamp = bars[k - 1]["timestamp"]
        sig = asyncio.run(strat.analyze("MNQ"))
        if sig is not None:
            k_at = k
            break
    assert sig is not None and k_at == len(bars)
    assert sig["action"] == "SHORT"
    assert sig["entry_price"] == 110.0
    assert "immediate_stop" in sig["reason"]


def test_analyze_emits_short_after_high_sweep_reentry(monkeypatch):
    # Legacy candle-close re-entry path: pin reentry_threshold_points=0 so the new
    # points-threshold default does not steal the trigger from the close check.
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_REENTRY_CLOSE", "true")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REENTRY_THRESHOLD_POINTS", "0")
    # See note in test_analyze_emits_short_immediate_on_first_close_outside_high —
    # synthetic 10-pt fixture range needs the production width filters disabled.
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MIN_RANGE_WIDTH_POINTS", "0")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_RANGE_WIDTH_POINTS", "0")
    # Production TOML ships sl_mult=0.5 (half-range stop). The expected stop=115
    # below was tuned to legacy class default sl_mult=1.0. Pin it here so the
    # test documents the geometry, not the live risk profile.
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_SL_MULT", "1.0")
    # 2026-05-29: pin tp_mult too — production root TOML now ships 1.5, the test
    # geometry below assumes 1.0 (TP at midpoint = 105 on a [100,110] range).
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_TP_MULT", "1.0")
    # 2026-05-29 walk-forward sweep: production TOML now ships root
    # ``sl_fixed_pts=35`` plus a ``[symbols.MNQ.signal]`` override with
    # ``sl_fixed_pts=50, tp_mult=1.25``. Both win over the env vars set above
    # because ``symbol_override`` checks the per-symbol TOML node before the
    # env-var chain. The test geometry below (stop=115, TP=105 on a 10-pt
    # range) only holds when neither the fixed-pts SL nor the MNQ override
    # is in play — so clear both *after* the strategy loads the config.
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_SL_FIXED_PTS", "0")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="morning_range_reversion",
        enabled=True,
        symbols=["MNQ"],
        max_positions=1,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=12,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )
    # 2026-01-07 7:00 ET = 12:00 UTC (EST)
    t0 = _utc(2026, 1, 7, 12, 0)
    bars = []
    for i in range(12):
        bars.append(
            {
                "timestamp": t0 + timedelta(minutes=5 * i),
                "open": 105,
                "high": 110,
                "low": 100,
                "close": 105,
                "volume": 1,
            }
        )
    bars.append(
        {
            "timestamp": t0 + timedelta(minutes=5 * 12),
            "open": 105,
            "high": 112,
            "low": 104,
            "close": 111,
            "volume": 1,
        }
    )
    bars.append(
        {
            "timestamp": t0 + timedelta(minutes=5 * 13),
            "open": 111,
            "high": 111,
            "low": 105,
            "close": 105,
            "volume": 1,
        }
    )
    # Replay calls analyze once per bar with a growing history (see strategy_replay.py).
    bot = _MockBot(bars[:1])
    strat = MorningRangeReversionStrategy(bot, cfg)
    # Strip the MNQ per-symbol overrides loaded from the live TOML so the test's
    # legacy ``sl_mult=1.0`` / ``tp_mult=1.0`` geometry holds.  Done post-construction
    # because ``_cfg`` is created in the strategy's ``__init__``.
    strat._cfg._data.get("symbols", {}).pop("MNQ", None)
    sig = None
    for k in range(1, len(bars) + 1):
        bot.bars = bars[:k]
        bot._current_bar_timestamp = bars[k - 1]["timestamp"]
        sig = asyncio.run(strat.analyze("MNQ"))
        if sig is not None:
            break
    assert sig is not None
    assert sig["action"] == "SHORT"
    assert sig["entry_price"] == 110.0
    assert sig["take_profit"] == 105.0
    assert sig["stop_loss"] == 115.0
    assert "reentry_confirm" in sig["reason"]


def test_sieve_threshold_mode_arms_on_sweep_bar_advance_stop(monkeypatch):
    """``reentry_threshold_points=7`` places advance stop-entry on the sweep bar close (not a later
    retrace bar) at L+7 / H-7 inside the box (overnight_range style)."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    # Use a wider range (H=130, L=100, width=30) so a 7pt threshold fits comfortably inside
    # half-width. The cap inside ``_fade_signal_after_sweep`` clamps depth at half-width.
    start = pd.Timestamp("2026-01-05 12:00", tz="UTC")
    idx = [start + pd.Timedelta(minutes=5 * i) for i in range(15)]
    rows = []
    for i in range(15):
        if i < 12:
            rows.append({"open": 115, "high": 130, "low": 100, "close": 115})
        elif i == 12:
            # High sweep close above the range — advance stop arms on this bar
            rows.append({"open": 115, "high": 135, "low": 114, "close": 133})
        elif i == 13:
            rows.append({"open": 133, "high": 133, "low": 121, "close": 132})
        else:
            rows.append({"open": 133, "high": 134, "low": 132, "close": 133})
    df = pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])
    tr_legacy = sieve_simulate_from_ohlcv(df, require_reentry_close=True)
    assert len(tr_legacy) == 0, [t.entry for t in tr_legacy]
    tr_thr = sieve_simulate_from_ohlcv(df, require_reentry_close=True, reentry_threshold_points=7.0)
    assert len(tr_thr) >= 1
    t0 = tr_thr[0]
    assert t0.side == "SHORT"
    assert t0.sweep == "high"
    assert t0.entry == pytest.approx(123.0, abs=0.01)
    assert t0.stop == pytest.approx(145.0, abs=0.01)
    assert t0.entry_ts == pd.Timestamp(idx[12].tz_convert(None))


def test_analyze_threshold_mode_default_emits_on_sweep_bar(monkeypatch):
    """Default TOML (reentry_threshold_points=7) places advance stop on the sweep bar close at H-7."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_REENTRY_CLOSE", "true")
    # Explicit env so the test is robust against future TOML default changes.
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REENTRY_THRESHOLD_POINTS", "7")
    # Fixture range = 30 pts (H=130, L=100). Production TOML now sets
    # min_range_width_points=50 (added 2026-05-21 to filter narrow days where
    # depth-capping makes entry≈midpoint≈TP and TP fills net negative). Bypass
    # so the unit test still exercises the threshold-mode signal path.
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MIN_RANGE_WIDTH_POINTS", "0")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_RANGE_WIDTH_POINTS", "0")
    # Expected stop_loss=145 was tuned to legacy class default sl_mult=1.0;
    # production TOML now ships sl_mult=0.5. Pin so the test documents geometry.
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_SL_MULT", "1.0")
    # 2026-05-29 sweep: also restore the range-anchored stop. Root TOML now
    # ships ``sl_fixed_pts=35`` and a MNQ override ``sl_fixed_pts=50``; both
    # would replace the half-range stop the rest of this test asserts on.
    # The env var clears the *root* knob; the per-symbol override is cleared
    # post-construction by the strip below (see comment near ``strat._cfg``).
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_SL_FIXED_PTS", "0")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="morning_range_reversion",
        enabled=True,
        symbols=["MNQ"],
        max_positions=1,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=12,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )
    t0 = _utc(2026, 1, 7, 12, 0)
    bars = []
    for i in range(12):
        bars.append({
            "timestamp": t0 + timedelta(minutes=5 * i),
            "open": 115, "high": 130, "low": 100, "close": 115, "volume": 1,
        })
    bars.append({
        "timestamp": t0 + timedelta(minutes=5 * 12),
        "open": 115, "high": 135, "low": 114, "close": 133, "volume": 1,
    })
    bars.append({
        "timestamp": t0 + timedelta(minutes=5 * 13),
        "open": 133, "high": 133, "low": 121, "close": 132, "volume": 1,
    })

    bot = _MockBot(bars[:1])
    strat = MorningRangeReversionStrategy(bot, cfg)
    # 2026-05-29 walk-forward sweep added a MNQ per-symbol slfix/tp override.
    # Drop it so the test geometry (stop=145 from half-range × sl_mult=1.0)
    # holds independent of live TOML changes.
    strat._cfg._data.get("symbols", {}).pop("MNQ", None)
    sig = None
    k_at = None
    for k in range(1, len(bars) + 1):
        bot.bars = bars[:k]
        bot._current_bar_timestamp = bars[k - 1]["timestamp"]
        sig = asyncio.run(strat.analyze("MNQ"))
        if sig is not None:
            k_at = k
            break
    assert sig is not None and k_at == 13
    assert sig["action"] == "SHORT"
    assert sig["entry_price"] == pytest.approx(123.0, abs=0.01)
    assert sig["stop_loss"] == pytest.approx(145.0, abs=0.01)
    assert "sweep_advance_stop" in sig["reason"]


def test_backtest_engine_rejects_wrong_side_buy_stop():
    """A BUY STOP placed *below* the current market must not fill — the old simulator filled it
    whenever a subsequent bar's high was above the stop, which is exactly the bug that produced
    the impossible mode-A entries on MNQ 2026-05-19."""
    from core.backtest.engine import BacktestEngine
    from core.backtest.models import OrderSide, OrderType

    eng = BacktestEngine(initial_capital=10000.0)
    eng.set_last_close(28910.0)  # Market is well above where we'd want to "BUY STOP"
    order_id = eng.place_order(
        symbol="MNQ",
        side=OrderSide.BUY,
        quantity=1,
        order_type=OrderType.STOP,
        stop_price=28853.75,  # Wrong side — below market
        price=28853.75,
    )
    pending = next(o for o in eng.pending_orders if o.order_id == order_id)
    assert pending.placement_price == pytest.approx(28910.0)

    bar = pd.Series({"open": 28910.75, "high": 28911.50, "low": 28868.75, "close": 28892.50},
                    name=pd.Timestamp("2026-05-19 13:45", tz="UTC"))
    # bar.high (28911.5) > stop (28853.75) so the old simulator would have filled here.
    filled = eng._check_order_fill(pending, bar, tick_size=0.25)
    assert filled is False
    assert pending.filled_price is None


def test_backtest_engine_allows_valid_stop_direction():
    """Sanity: a normal SELL STOP placed *below* current market still fills when bar.low <= stop."""
    from core.backtest.engine import BacktestEngine
    from core.backtest.models import OrderSide, OrderType

    eng = BacktestEngine(initial_capital=10000.0)
    eng.set_last_close(28910.0)
    order_id = eng.place_order(
        symbol="MNQ",
        side=OrderSide.SELL,
        quantity=1,
        order_type=OrderType.STOP,
        stop_price=28902.5,  # Below current market — valid SELL STOP
        price=28902.5,
    )
    pending = next(o for o in eng.pending_orders if o.order_id == order_id)
    bar = pd.Series({"open": 28910.0, "high": 28912.0, "low": 28890.0, "close": 28895.0},
                    name=pd.Timestamp("2026-05-19 13:45", tz="UTC"))
    filled = eng._check_order_fill(pending, bar, tick_size=0.25)
    assert filled is True
    assert pending.filled_price == pytest.approx(28902.5 - 0.125, abs=0.001)


def test_morning_range_toml_has_et_executor_window():
    """Stock TOML widens ``should_trade`` before 07:00 ET and uses empty no-trade band."""
    from pathlib import Path
    import tomllib

    path = Path(__file__).resolve().parent.parent / "config/strategies/morning_range_reversion.toml"
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    assert data.get("start_time") == "06:55"
    assert data.get("end_time") == "16:00"
    assert data.get("no_trade_start") == ""
    assert data.get("no_trade_end") == ""
    assert isinstance(data.get("meta", {}).get("enabled"), bool)


def test_in_trading_window_uses_session_timezone_not_local_naive_clock(monkeypatch):
    """Regression: executor gate uses ``_session_tz_wall_now`` (session TZ), not naive local clock."""
    from zoneinfo import ZoneInfo

    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from strategies.strategy_base import StrategyConfig

    z = ZoneInfo("America/New_York")
    cfg = StrategyConfig(
        name="morning_range_reversion",
        enabled=True,
        symbols=["MNQ"],
        max_positions=1,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=4,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="06:55",
        trading_end_time="16:00",
        no_trade_start="",
        no_trade_end="",
    )
    bot = _MockBot([])
    bot._is_strategy_replay = False
    strat = MorningRangeReversionStrategy(bot, cfg)

    monkeypatch.setattr(strat, "_session_tz_wall_now", lambda: datetime(2026, 5, 12, 7, 30, tzinfo=z))
    assert strat._in_trading_window() is True
    monkeypatch.setattr(strat, "_session_tz_wall_now", lambda: datetime(2026, 5, 12, 6, 30, tzinfo=z))
    assert strat._in_trading_window() is False
    monkeypatch.setattr(strat, "_session_tz_wall_now", lambda: datetime(2026, 5, 12, 16, 1, tzinfo=z))
    assert strat._in_trading_window() is False

    bot._is_strategy_replay = True
    assert strat._in_trading_window() is True


# ── R-geometry / range-width filter tests ────────────────────────────────────

def _make_range_df(range_width: float, sweep: str = "low"):
    """Minimal sieve DataFrame: range bars then a sweep bar."""
    import numpy as np

    rows = []
    # 7:00–7:55 ET = 11:00–11:55 UTC on a winter day
    t0 = pd.Timestamp("2026-01-07 12:00:00", tz="UTC").tz_localize(None)
    mid_price = 200.0
    H = mid_price + range_width / 2
    L = mid_price - range_width / 2
    for i in range(12):  # 12 × 5m = 60 min range window
        rows.append({"open": mid_price, "high": H, "low": L, "close": mid_price})
    # First close outside: sweep bar
    if sweep == "low":
        rows.append({"open": L - 1, "high": L - 0.5, "low": L - 5, "close": L - 4})
    else:
        rows.append({"open": H + 1, "high": H + 5, "low": H + 0.5, "close": H + 4})
    # A few more bars so the stop-entry can fill
    for _ in range(5):
        rows.append({"open": mid_price, "high": H + 1, "low": L - 1, "close": mid_price})
    df = pd.DataFrame(rows)
    idx = [t0 + pd.Timedelta(minutes=5 * i) for i in range(len(rows))]
    df.index = pd.DatetimeIndex(idx)
    return df, H, L


def test_sieve_min_range_width_skips_narrow_range():
    """Trades with width < min_range_width_points are skipped entirely (fixes TP-in-loss bug)."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    df, _, _ = _make_range_df(range_width=10.0)
    # Without filter: should produce a trade (even though TP ≈ entry for 10pt range)
    trades_no_filter = sieve_simulate_from_ohlcv(df, reentry_threshold_points=7.0)
    assert len(trades_no_filter) >= 1

    # With filter requiring ≥ 20 pts: must skip this 10pt range day entirely
    trades_filtered = sieve_simulate_from_ohlcv(
        df, reentry_threshold_points=7.0, min_range_width_points=20.0
    )
    assert len(trades_filtered) == 0


def test_sieve_max_range_width_skips_wide_range():
    """Trades with width > max_range_width_points are skipped (caps oversized risk)."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    df, _, _ = _make_range_df(range_width=200.0)
    trades_no_filter = sieve_simulate_from_ohlcv(df, reentry_threshold_points=7.0)
    assert len(trades_no_filter) >= 1

    trades_filtered = sieve_simulate_from_ohlcv(
        df, reentry_threshold_points=7.0, max_range_width_points=100.0
    )
    assert len(trades_filtered) == 0


def test_sieve_sl_fixed_pts_places_stop_relative_to_entry():
    """sl_fixed_pts places stop sl_fixed_pts away from entry, not at L-half."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    df, H, L = _make_range_df(range_width=60.0, sweep="low")
    threshold = 7.0
    sl_pts = 14.0

    # Without sl_fixed_pts: stop at L - half = L - 30
    trades_legacy = sieve_simulate_from_ohlcv(
        df, reentry_threshold_points=threshold, sl_fixed_pts=0.0
    )
    # With sl_fixed_pts: stop at entry - sl_pts = (L + 7) - 14 = L - 7
    trades_fixed = sieve_simulate_from_ohlcv(
        df, reentry_threshold_points=threshold, sl_fixed_pts=sl_pts
    )

    assert len(trades_legacy) == len(trades_fixed) == 1
    t_legacy = trades_legacy[0]
    t_fixed = trades_fixed[0]

    half = 30.0
    expected_stop_legacy = pytest.approx(L - half, abs=0.5)
    expected_stop_fixed = pytest.approx(t_fixed.entry - sl_pts, abs=0.5)

    assert t_legacy.stop == expected_stop_legacy
    assert t_fixed.stop == expected_stop_fixed
    # Fixed stop is much tighter (closer to entry) than legacy
    assert abs(t_fixed.stop - t_fixed.entry) < abs(t_legacy.stop - t_legacy.entry)


# ── Live data-freshness guard tests ──────────────────────────────────────────

class _LiveMockBot:
    """Mock bot that does NOT set ``_is_strategy_replay`` — guard runs against wall-clock."""

    def __init__(self, bars):
        self.bars = bars
        self.selected_account = {"id": "test", "name": "TEST"}
        self._is_strategy_replay = False
        self._current_bar_timestamp = None

    async def get_historical_data(self, symbol, timeframe=None, limit=None, **_):
        if limit:
            return self.bars[-limit:]
        return self.bars

    async def get_open_positions(self, account_id=None, **_):
        return []


def _live_cfg():
    from strategies.strategy_base import StrategyConfig

    return StrategyConfig(
        name="morning_range_reversion",
        enabled=True,
        symbols=["MNQ"],
        max_positions=1,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=4,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )


def _bars_ending_at(end_ts, n=20, step_minutes=5):
    """Make ``n`` bars whose last timestamp is ``end_ts`` (i.e., walking backward from end)."""
    return [
        {
            "timestamp": end_ts - timedelta(minutes=step_minutes * (n - 1 - i)),
            "open": 100, "high": 110, "low": 90, "close": 100, "volume": 1,
        }
        for i in range(n)
    ]


def test_freshness_guard_aborts_when_last_bar_older_than_threshold(monkeypatch, caplog):
    """Recreates 2026-05-21 outage: REST returned bars frozen 70+ min behind wall clock."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_BAR_STALENESS_SECONDS", "600")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    last_ts = datetime.now(timezone.utc) - timedelta(hours=1, minutes=10)  # 70 min behind
    bars = _bars_ending_at(last_ts)
    bot = _LiveMockBot(bars)
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    with caplog.at_level("ERROR", logger="strategies.morning_range_reversion_strategy"):
        result = asyncio.run(strat.analyze("MNQ"))

    assert result is None
    assert any("STALE DATA" in r.getMessage() for r in caplog.records)


def test_freshness_guard_throttles_repeated_log_within_60s(monkeypatch, caplog):
    """Three back-to-back analyze calls with stale data → only one ERROR line (60s throttle)."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_BAR_STALENESS_SECONDS", "600")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    last_ts = datetime.now(timezone.utc) - timedelta(hours=1, minutes=10)
    bars = _bars_ending_at(last_ts)
    bot = _LiveMockBot(bars)
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    with caplog.at_level("ERROR", logger="strategies.morning_range_reversion_strategy"):
        for _ in range(3):
            assert asyncio.run(strat.analyze("MNQ")) is None

    stale_records = [r for r in caplog.records if "STALE DATA" in r.getMessage()]
    assert len(stale_records) == 1, "guard should throttle to 1 ERROR within 60s"


def test_freshness_guard_passes_through_fresh_bars(monkeypatch):
    """Bars within the threshold do not trip the guard."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_BAR_STALENESS_SECONDS", "600")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    last_ts = datetime.now(timezone.utc) - timedelta(minutes=2)  # within 600s threshold
    bars = _bars_ending_at(last_ts)
    bot = _LiveMockBot(bars)
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    assert strat._bars_are_stale("MNQ", bars) is False


def test_freshness_guard_bypassed_during_replay(monkeypatch):
    """Replay (``_is_strategy_replay=True``) bypasses the guard even on bars that are hours old."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_BAR_STALENESS_SECONDS", "600")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    last_ts = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    bars = _bars_ending_at(last_ts)
    bot = _MockBot(bars)  # _MockBot sets _is_strategy_replay = True
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    assert strat._bars_are_stale("MNQ", bars) is False


def test_freshness_guard_disabled_when_threshold_zero(monkeypatch):
    """Setting threshold = 0 disables the guard (legacy behaviour for opt-out)."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_BAR_STALENESS_SECONDS", "0")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    last_ts = datetime.now(timezone.utc) - timedelta(hours=6)
    bars = _bars_ending_at(last_ts)
    bot = _LiveMockBot(bars)
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    assert strat._bars_are_stale("MNQ", bars) is False


# ─────────────────────────────────────────────────────────────────────────────
# Mid-session start backfill (2026-05-27 fix)
#
# Repro for the user-reported "feels like every time I run the actual script
# through headless strategy executor it does nothing and just goes stale, never
# actually places or attempts to place any orders". Root cause: the per-bar
# state machine in ``analyze()`` only accumulates ``range_hi``/``range_lo`` on
# bars whose timestamp falls in ``[range_start, range_end_open)`` and it uses
# ``bars[-1]`` per invocation. If the executor is launched **after** the
# anchor window closes, the first invocation sees ``bars[-1]`` past
# ``range_end_open``, never enters the build branch, and finalises from an
# empty state → marks the symbol idle for the day.
#
# The fix is ``_seed_range_from_history``: scan the already-fetched bars for
# completed bars inside the anchor window and pre-seed H/L before the per-bar
# branch runs. These tests prove the seed picks the right bars, behaves
# correctly when the executor is started during/before the window, and that
# the end-to-end analyze() flow ARMS the fade scanner instead of going idle.
# ─────────────────────────────────────────────────────────────────────────────


def _utc_tz(*a, **kw):
    return datetime(*a, tzinfo=timezone.utc, **kw)


def _build_anchor_window_bars(session_date, n_build=12, n_post=1):
    """Return 5m bars spanning the morning_range_reversion anchor window plus N post-anchor bars.

    Anchor window in ET = ``[07:00, 08:00)`` → 12 bars (07:00, 07:05, ..., 07:55).
    Each post-anchor bar advances 5 min past 08:00 ET (= 12:00 UTC equivalent in EST/EDT).
    ``session_date`` should be a ``datetime.date`` for the ET trading day; bars are
    constructed in UTC at the EDT offset (-04:00) to keep the test self-contained.
    """
    # EDT offset: 07:00 ET = 11:00 UTC during DST, 12:00 UTC during EST.
    # 2026-05-27 is during EDT (DST in effect): 07:00 ET = 11:00 UTC.
    et_offset_hours = 4  # EDT; tests fix the date to a DST day below
    base_utc = datetime(session_date.year, session_date.month, session_date.day,
                        7 + et_offset_hours, 0, tzinfo=timezone.utc)
    bars = []
    for i in range(n_build):
        h = 110.0 + (i if i < 5 else 0)
        l = 100.0 - (1 if i == 7 else 0)
        bars.append({
            "timestamp": base_utc + timedelta(minutes=5 * i),
            "open": 105.0, "high": h, "low": l, "close": 105.0, "volume": 1,
        })
    post_start = base_utc + timedelta(minutes=5 * n_build)
    for j in range(n_post):
        bars.append({
            "timestamp": post_start + timedelta(minutes=5 * j),
            "open": 105.0, "high": 110.0, "low": 100.0, "close": 105.0, "volume": 1,
        })
    return bars


def test_seed_range_from_history_captures_full_anchor_window():
    """Seed pulls H=max(highs) / L=min(lows) over the 12 anchor build bars only."""
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from datetime import date

    session = date(2026, 5, 27)
    bars = _build_anchor_window_bars(session, n_build=12, n_post=2)
    cfg = _live_cfg()
    bot = _MockBot(bars)
    strat = MorningRangeReversionStrategy(bot, cfg)

    hi, lo, n = strat._seed_range_from_history(bars, session)
    assert n == 12, f"expected 12 build-window bars, got {n}"
    assert hi == 114.0, f"max of highs should be 110+(4)=114, got {hi}"
    assert lo == 99.0, f"min of lows should be 99 (bar i=7 has low=99), got {lo}"


def test_seed_range_from_history_excludes_post_anchor_bars():
    """Seed must NOT pick up bars whose ET timestamp >= range_end_open (08:00 ET)."""
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from datetime import date

    session = date(2026, 5, 27)
    # Build 12 anchor bars + 5 post-anchor bars; spike a post-anchor high to confirm exclusion
    bars = _build_anchor_window_bars(session, n_build=12, n_post=5)
    bars[-1]["high"] = 200.0  # would obviously break things if seeded
    cfg = _live_cfg()
    bot = _MockBot(bars)
    strat = MorningRangeReversionStrategy(bot, cfg)

    hi, lo, n = strat._seed_range_from_history(bars, session)
    assert n == 12
    assert hi < 150.0, f"post-anchor 200.0 spike must not be seeded, got hi={hi}"


def test_seed_range_from_history_excludes_other_session_dates():
    """Bars from prior trading days must be filtered out (400-bar fetch can span ~33h)."""
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from datetime import date

    session = date(2026, 5, 27)
    today_bars = _build_anchor_window_bars(session, n_build=12, n_post=2)
    # Yesterday: same anchor window UTC times but on May 26 → should be excluded.
    yesterday = date(2026, 5, 26)
    y_bars = _build_anchor_window_bars(yesterday, n_build=12, n_post=2)
    for b in y_bars:
        b["high"] = 500.0  # would break the seed if not filtered out
    bars = y_bars + today_bars
    cfg = _live_cfg()
    bot = _MockBot(bars)
    strat = MorningRangeReversionStrategy(bot, cfg)

    hi, lo, n = strat._seed_range_from_history(bars, session)
    assert n == 12
    assert hi < 200.0, f"prior session must not bleed into seed, got hi={hi}"


def test_seed_range_from_history_returns_empty_when_no_build_bars_yet():
    """Executor started BEFORE the anchor window → no completed build bars yet → (None, None, 0)."""
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from datetime import date

    session = date(2026, 5, 27)
    # Only pre-anchor (06:00-06:55 ET) bars
    et_offset_hours = 4
    base_utc = datetime(2026, 5, 27, 6 + et_offset_hours, 0, tzinfo=timezone.utc)
    bars = [
        {"timestamp": base_utc + timedelta(minutes=5 * i),
         "open": 105, "high": 110, "low": 100, "close": 105, "volume": 1}
        for i in range(12)
    ]
    cfg = _live_cfg()
    bot = _MockBot(bars)
    strat = MorningRangeReversionStrategy(bot, cfg)

    hi, lo, n = strat._seed_range_from_history(bars, session)
    assert n == 0
    assert hi is None and lo is None


def test_mid_session_start_finalises_range_and_arms_scanner(monkeypatch):
    """End-to-end regression for 2026-05-27 bot start.

    Before the fix: launching the executor at 08:07 ET with bars[-1] = 08:05 ET would
    finalise from an empty state → ``range_ready=True, phase='idle'`` → no trades all day.

    After the fix: the first ``analyze()`` invocation backfills H/L from the 12 anchor
    bars in history, finalises the range, and transitions to ``phase='scan'`` armed
    for fade detection.
    """
    # The synthetic anchor-window fixture builds a 15pt range; production TOML now
    # ships min_range_width_points=20 (calibrated for typical MNQ pre-market widths).
    # Disable the width filter for this geometry test so the assertion lands on
    # phase='scan' (the actual contract under test), not on the per-symbol filter.
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MIN_RANGE_WIDTH_POINTS", "0")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_RANGE_WIDTH_POINTS", "0")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from datetime import date

    session = date(2026, 5, 27)
    bars = _build_anchor_window_bars(session, n_build=12, n_post=2)
    cfg = _live_cfg()
    bot = _MockBot(bars)  # _MockBot sets _is_strategy_replay so freshness guard is bypassed
    strat = MorningRangeReversionStrategy(bot, cfg)

    # Drive a single analyze() with bars[-1] already PAST the anchor close (08:05 ET = 12:05 UTC EDT).
    asyncio.run(strat.analyze("MNQ"))

    st = strat._state["MNQ"]
    assert st["range_ready"] is True, "range must be finalised by mid-session start backfill"
    assert st["phase"] == "scan", f"expected phase='scan' after backfill, got phase={st['phase']!r}"
    assert st["range_hi"] is not None and st["range_lo"] is not None
    assert st["H"] == 114.0 and st["L"] == 99.0, f"finalised H/L should match seeded values, got H={st['H']} L={st['L']}"


def test_mid_session_start_logs_backfill_summary(caplog, monkeypatch):
    """A diagnostic log line confirms the seed fired so operators see WHY the strategy is armed."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MIN_RANGE_WIDTH_POINTS", "0")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_RANGE_WIDTH_POINTS", "0")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from datetime import date

    session = date(2026, 5, 27)
    bars = _build_anchor_window_bars(session, n_build=12, n_post=2)
    cfg = _live_cfg()
    bot = _MockBot(bars)
    strat = MorningRangeReversionStrategy(bot, cfg)

    with caplog.at_level("INFO", logger="strategies.morning_range_reversion_strategy"):
        asyncio.run(strat.analyze("MNQ"))

    backfill_lines = [r for r in caplog.records if "anchor backfill from history" in r.getMessage()]
    assert len(backfill_lines) == 1, "exactly one backfill INFO log per (symbol, session) expected"
    assert "12 build-window bar(s)" in backfill_lines[0].getMessage()
    assert "past anchor close" in backfill_lines[0].getMessage()


# ─────────────────────────────────────────────────────────────────────────────
# Range-width pre-filter (2026-05-29 fix)
#
# Before: when ``min_range_width_points``/``max_range_width_points`` rejected a
# day, the strategy still logged ``📐 fade scanner armed`` and only dropped
# matches at ``logger.debug`` inside ``_fade_signal_after_sweep``. Net effect:
# the operator saw "armed" for symbols that could never produce a signal, and
# burned analyze cycles all day for nothing. The 2026-05-29 log shows MES
# (width=11.75) and MGC (width=15.10) both logged "armed filter=20≤w≤300"
# even though their widths were below the 20pt minimum.
#
# After: the filter is evaluated at range-finalisation; failing widths idle
# the symbol with a one-shot WARNING that names the offending number and
# points at the per-symbol override section.
# ─────────────────────────────────────────────────────────────────────────────


def _build_anchor_bars_with_width(session_date, target_width: float, n_post: int = 2):
    """Build anchor-window bars whose H/L produce exactly ``target_width`` pts."""
    et_offset_hours = 4  # EDT
    base_utc = datetime(session_date.year, session_date.month, session_date.day,
                        7 + et_offset_hours, 0, tzinfo=timezone.utc)
    low = 100.0
    high = low + float(target_width)
    bars = []
    for i in range(12):
        bars.append({
            "timestamp": base_utc + timedelta(minutes=5 * i),
            "open": (low + high) / 2,
            "high": high if i == 0 else (low + high) / 2,
            "low": low if i == 0 else (low + high) / 2,
            "close": (low + high) / 2,
            "volume": 1,
        })
    post_start = base_utc + timedelta(minutes=5 * 12)
    for j in range(n_post):
        bars.append({
            "timestamp": post_start + timedelta(minutes=5 * j),
            "open": (low + high) / 2,
            "high": (low + high) / 2,
            "low": (low + high) / 2,
            "close": (low + high) / 2,
            "volume": 1,
        })
    return bars


def test_filter_rejects_narrow_range_at_build_time_and_idles_symbol(monkeypatch, caplog):
    """Repro 2026-05-29 MES path (using MNQ as the no-per-symbol-override carrier symbol).

    Forces min=20 on MNQ (root path — MNQ has no per-symbol override) and feeds a
    width=11.75 synthetic range that matches the live log's MES geometry. Behaviour
    must be: state ``range_ready=True, phase='idle'`` with exactly one WARNING log
    that names the offending width and points at the override section.
    """
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MIN_RANGE_WIDTH_POINTS", "20")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_RANGE_WIDTH_POINTS", "300")
    # Use Tuesday 2026-05-26 — non-skipped day for every committed per-symbol
    # weekday filter (MNQ skips Mon/Thu/Fri; MGC skips Wed/Fri; MES inherits
    # root Fri).  This isolates the width-filter behaviour from the weekday gate.
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from datetime import date

    session = date(2026, 5, 26)
    bars = _build_anchor_bars_with_width(session, target_width=11.75)
    bot = _MockBot(bars)
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    with caplog.at_level("WARNING", logger="strategies.morning_range_reversion_strategy"):
        result = asyncio.run(strat.analyze("MNQ"))

    st = strat._state["MNQ"]
    assert result is None
    assert st["range_ready"] is True
    assert st["phase"] == "idle", f"narrow range must idle the symbol, got phase={st['phase']!r}"
    narrow_logs = [r for r in caplog.records if "anchor range too narrow" in r.getMessage()]
    assert len(narrow_logs) == 1, f"expected exactly one WARNING about narrow range, got {len(narrow_logs)}"
    msg = narrow_logs[0].getMessage()
    assert "width=11.75pts" in msg and "min=20.00pts" in msg
    assert "[symbols.MNQ.signal]" in msg, "log should point at the per-symbol override section"


def test_filter_rejects_wide_range_at_build_time_and_idles_symbol(monkeypatch, caplog):
    """Wide range → idle with explicit ``too wide`` warning (defence against oversized risk days)."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MIN_RANGE_WIDTH_POINTS", "20")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_RANGE_WIDTH_POINTS", "100")
    # Tuesday 2026-05-26 — not skipped by any per-symbol weekday filter.
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from datetime import date

    session = date(2026, 5, 26)
    bars = _build_anchor_bars_with_width(session, target_width=150.0)
    bot = _MockBot(bars)
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    with caplog.at_level("WARNING", logger="strategies.morning_range_reversion_strategy"):
        asyncio.run(strat.analyze("MNQ"))

    st = strat._state["MNQ"]
    assert st["range_ready"] is True
    assert st["phase"] == "idle"
    wide_logs = [r for r in caplog.records if "anchor range too wide" in r.getMessage()]
    assert len(wide_logs) == 1
    assert "width=150.00pts" in wide_logs[0].getMessage()
    assert "max=100.00pts" in wide_logs[0].getMessage()


def test_filter_warning_fires_exactly_once_per_symbol_per_session(monkeypatch, caplog):
    """Multiple analyze() calls on a stuck-narrow day must not spam the WARN log."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MIN_RANGE_WIDTH_POINTS", "20")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_RANGE_WIDTH_POINTS", "300")
    # Tuesday 2026-05-26 — not skipped by any per-symbol weekday filter.
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from datetime import date

    session = date(2026, 5, 26)
    bars = _build_anchor_bars_with_width(session, target_width=11.75)
    bot = _MockBot(bars)
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    with caplog.at_level("WARNING", logger="strategies.morning_range_reversion_strategy"):
        for _ in range(5):
            asyncio.run(strat.analyze("MNQ"))

    narrow_logs = [r for r in caplog.records if "anchor range too narrow" in r.getMessage()]
    assert len(narrow_logs) == 1, "throttle guard via _logged_range_degenerate must fire exactly once"


def test_stale_data_downgraded_to_warning_when_session_recently_rotated(caplog):
    """2026-05-29 fix: when the bot just rotated its HTTP session (cold-start
    detector tripped), the strategy's STALE DATA log must downgrade from ERROR
    to WARNING. The operator already saw '🧊 REST feed cold-start stale' +
    '🔁 HTTP session reset' — surfacing the same situation as ERROR right after
    is misleading and creates an ERROR storm during a normal cold-start recovery.
    """
    import time as _time
    from datetime import datetime, timezone, timedelta
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    cfg = _live_cfg()
    # Use _LiveMockBot so the freshness guard actually runs (it bypasses for replay bots).
    bot = _LiveMockBot([])
    # Mark "we just rotated the session 0.5s ago".
    bot._last_session_reset_at_mono = _time.monotonic() - 0.5
    bot._session_reset_cooldown_s = 10.0

    strat = MorningRangeReversionStrategy(bot, cfg)
    stale_ts = datetime.now(timezone.utc) - timedelta(seconds=1500)
    bars = [{"timestamp": stale_ts, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}]

    with caplog.at_level("WARNING", logger="strategies.morning_range_reversion_strategy"):
        was_stale = strat._bars_are_stale("MNQ", bars)

    assert was_stale is True
    warnings_about_stale = [
        r for r in caplog.records
        if r.levelname == "WARNING" and "STALE DATA for MNQ" in r.getMessage()
    ]
    errors_about_stale = [
        r for r in caplog.records
        if r.levelname == "ERROR" and "STALE DATA for MNQ" in r.getMessage()
    ]
    assert len(warnings_about_stale) == 1, (
        f"expected 1 WARNING during active recovery, got {len(warnings_about_stale)} "
        f"warnings + {len(errors_about_stale)} errors"
    )
    assert len(errors_about_stale) == 0, "no ERROR while session is actively being rotated"
    msg = warnings_about_stale[0].getMessage()
    assert "rotated" in msg.lower()


def test_stale_data_still_logs_error_when_no_recent_session_rotation(caplog):
    """Conversely: if NO recent rotation, STALE DATA keeps the original ERROR
    severity — that's a real broker outage and the operator needs to see it."""
    from datetime import datetime, timezone, timedelta
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    cfg = _live_cfg()
    bot = _LiveMockBot([])
    # ``None`` sentinel = no rotation has ever happened (the bot's default).
    bot._last_session_reset_at_mono = None
    bot._session_reset_cooldown_s = 10.0

    strat = MorningRangeReversionStrategy(bot, cfg)
    stale_ts = datetime.now(timezone.utc) - timedelta(seconds=1500)
    bars = [{"timestamp": stale_ts, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}]

    with caplog.at_level("WARNING", logger="strategies.morning_range_reversion_strategy"):
        was_stale = strat._bars_are_stale("MNQ", bars)

    assert was_stale is True
    errors = [
        r for r in caplog.records
        if r.levelname == "ERROR" and "STALE DATA for MNQ" in r.getMessage()
    ]
    assert len(errors) == 1, "no recent rotation → keep ERROR severity for the real outage"


def test_anchor_range_built_log_includes_pass_verdict_for_accepted_range(monkeypatch, caplog):
    """The 2026-05-29 lifecycle log must include the explicit ``✓`` verdict and
    show the filter bounds inline so the operator can eyeball ``which symbols
    will trade today`` at startup.

    Format (from user request):
        ``📐 ... anchor range built for ...: H=... L=... width=...pts mid=...
        filter=20≤w≤300 — fade scanner armed ← 73.25 ≥ 20  ✓``
    """
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MIN_RANGE_WIDTH_POINTS", "20")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_RANGE_WIDTH_POINTS", "300")
    # Tuesday 2026-05-26 — not skipped by any per-symbol weekday filter.
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from datetime import date

    session = date(2026, 5, 26)
    bars = _build_anchor_bars_with_width(session, target_width=73.25)
    bot = _MockBot(bars)
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    with caplog.at_level("INFO", logger="strategies.morning_range_reversion_strategy"):
        asyncio.run(strat.analyze("MNQ"))

    built_logs = [r for r in caplog.records if "anchor range built" in r.getMessage()]
    assert len(built_logs) == 1, f"expected one '📐 anchor range built' log, got {len(built_logs)}"
    msg = built_logs[0].getMessage()
    # Must include the bounds and the verdict mark.
    assert "filter=20≤w≤300" in msg, f"filter spec must show bounds inline, got: {msg!r}"
    assert "✓" in msg, f"accepted range must include ✓ verdict, got: {msg!r}"
    assert "fade scanner armed" in msg
    # Width must appear in the verdict explanation.
    assert "73.25" in msg


def test_anchor_range_built_log_includes_fail_verdict_for_narrow_range(monkeypatch, caplog):
    """Rejected (narrow) range must emit the SAME unified ``📐`` line with ``✗``
    and ``IDLED`` so all symbols can be compared side-by-side."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MIN_RANGE_WIDTH_POINTS", "20")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_RANGE_WIDTH_POINTS", "300")
    # Tuesday 2026-05-26 — not skipped by any per-symbol weekday filter.
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from datetime import date

    session = date(2026, 5, 26)
    bars = _build_anchor_bars_with_width(session, target_width=11.75)
    bot = _MockBot(bars)
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    with caplog.at_level("INFO", logger="strategies.morning_range_reversion_strategy"):
        asyncio.run(strat.analyze("MNQ"))

    built_logs = [r for r in caplog.records if "anchor range built" in r.getMessage()]
    assert len(built_logs) == 1
    msg = built_logs[0].getMessage()
    assert "IDLED" in msg, f"failed filter must show IDLED instead of 'fade scanner armed': {msg!r}"
    assert "✗" in msg, f"failed range must include ✗ verdict, got: {msg!r}"
    assert "11.75" in msg
    assert "< 20" in msg


def test_per_symbol_min_range_width_unblocks_mes_and_mgc(monkeypatch):
    """End-to-end repro 2026-05-29 fix: the LIVE TOML's per-symbol overrides for MES/MGC
    must accept the widths from the log (MES=11.75, MGC=15.10) and reach phase='scan'.

    This is the contract that makes the 2026-05-29 fix real for the live bot: read the
    live config (which now ships `[symbols.MES.signal].min_range_width_points = 3` and
    `[symbols.MGC.signal].min_range_width_points = 4`) and confirm the smaller-tick
    contracts arm correctly at their typical widths. MNQ at 73.25 (above root min=20)
    confirms the MNQ path is unchanged.
    """
    # Tuesday 2026-05-26 — not skipped by any per-symbol weekday filter.
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from datetime import date

    session = date(2026, 5, 26)
    cfg = _live_cfg()

    geometries = [("MNQ", 73.25), ("MES", 11.75), ("MGC", 15.10)]
    for sym, width in geometries:
        bars = _build_anchor_bars_with_width(session, target_width=width)
        bot = _MockBot(bars)
        strat = MorningRangeReversionStrategy(bot, cfg)
        asyncio.run(strat.analyze(sym))
        st = strat._state[sym]
        assert st["phase"] == "scan", (
            f"{sym} width={width} expected phase='scan' (per-symbol override should unblock it), "
            f"got {st['phase']!r}. The live TOML's [symbols.{sym}.signal] block likely lost "
            f"its min_range_width_points override."
        )
        assert st["range_hi"] is not None and st["range_lo"] is not None


def test_no_backfill_when_executor_starts_before_anchor_window():
    """Launching before 07:00 ET must NOT seed (no completed build bars yet)."""
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from datetime import date

    session = date(2026, 5, 27)
    et_offset_hours = 4
    base_utc = datetime(2026, 5, 27, 6 + et_offset_hours, 0, tzinfo=timezone.utc)
    # Only pre-anchor bars (06:00-06:55 ET)
    bars = [
        {"timestamp": base_utc + timedelta(minutes=5 * i),
         "open": 105, "high": 110, "low": 100, "close": 105, "volume": 1}
        for i in range(12)
    ]
    cfg = _live_cfg()
    bot = _MockBot(bars)
    strat = MorningRangeReversionStrategy(bot, cfg)

    asyncio.run(strat.analyze("MNQ"))

    st = strat._state["MNQ"]
    # Pre-anchor: state initialised but neither range_hi nor range_lo populated yet
    assert st["range_ready"] is False
    assert st["range_hi"] is None and st["range_lo"] is None


# ── Weekday-skip gate (2026-05-29 walk-forward sweep) ──────────────────────


def test_skip_weekdays_fri_suppresses_friday_session(monkeypatch, caplog):
    """``signal.skip_weekdays = ["Fri"]`` must short-circuit the new-session
    block on a Friday: state goes to ``phase='idle'`` with ``range_ready=False``
    and a single 🚫 lifecycle log fires per (symbol, date)."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_SKIP_WEEKDAYS", "Fri")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from datetime import date

    session = date(2026, 5, 29)  # Friday
    assert session.weekday() == 4
    bars = _build_anchor_bars_with_width(session, target_width=73.25)
    bot = _MockBot(bars)
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    with caplog.at_level("INFO", logger="strategies.morning_range_reversion_strategy"):
        result = asyncio.run(strat.analyze("MNQ"))

    assert result is None
    st = strat._state["MNQ"]
    assert st["phase"] == "idle"
    assert st["range_ready"] is False
    skip_logs = [r for r in caplog.records if "session skipped" in r.getMessage()]
    assert len(skip_logs) == 1, f"expected one 🚫 skip log, got {len(skip_logs)}"
    msg = skip_logs[0].getMessage()
    assert "Fri" in msg and "2026-05-29" in msg


def test_skip_weekdays_throttles_to_one_log_per_session(monkeypatch, caplog):
    """Multiple ``analyze()`` calls on the same skipped day must not spam the log."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_SKIP_WEEKDAYS", "Fri")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from datetime import date

    session = date(2026, 5, 29)
    bars = _build_anchor_bars_with_width(session, target_width=73.25)
    bot = _MockBot(bars)
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    with caplog.at_level("INFO", logger="strategies.morning_range_reversion_strategy"):
        for _ in range(5):
            asyncio.run(strat.analyze("MNQ"))

    skip_logs = [r for r in caplog.records if "session skipped" in r.getMessage()]
    assert len(skip_logs) == 1


def test_skip_weekdays_empty_trades_all_days(monkeypatch):
    """``signal.skip_weekdays = ""`` (empty) restores legacy behaviour: every
    weekday is traded, including Friday. Width-filter tests rely on this path
    to remain functional even with the new gate landed.

    NOTE: this test exercises the **root** weekday gate via env override.
    Live TOML now ships per-symbol overrides (``MNQ=[Mon,Thu,Fri]``,
    ``MGC=[Wed,Fri]``); the test patches ``_skip_weekdays`` to return an
    empty set so the env-override semantic can be observed without TOML
    per-symbol overrides preempting it (they take precedence by design).
    """
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_SKIP_WEEKDAYS", "")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from datetime import date

    session = date(2026, 5, 29)  # Friday
    bars = _build_anchor_bars_with_width(session, target_width=73.25)
    bot = _MockBot(bars)
    strat = MorningRangeReversionStrategy(bot, _live_cfg())
    monkeypatch.setattr(strat, "_skip_weekdays", lambda _sym: frozenset())

    asyncio.run(strat.analyze("MNQ"))
    st = strat._state["MNQ"]
    assert st["range_ready"] is True
    assert st["phase"] == "scan"


def test_skip_weekdays_does_not_skip_non_listed_day(monkeypatch):
    """Non-listed weekdays must still build their anchor range under the same
    ``skip_weekdays=["Fri"]`` env setting.

    Uses Tuesday 2026-05-26 — not skipped by any per-symbol TOML override
    (MNQ skips Mon/Thu/Fri; MGC skips Wed/Fri).  This isolates the env-driven
    root-weekday gate from the per-symbol TOML overrides."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_SKIP_WEEKDAYS", "Fri")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from datetime import date

    session = date(2026, 5, 26)  # Tuesday
    assert session.weekday() == 1
    bars = _build_anchor_bars_with_width(session, target_width=73.25)
    bot = _MockBot(bars)
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    asyncio.run(strat.analyze("MNQ"))
    st = strat._state["MNQ"]
    assert st["range_ready"] is True
    assert st["phase"] == "scan"


def test_skip_weekdays_accepts_int_and_name_tokens(monkeypatch):
    """``skip_weekdays`` accepts either ``["Fri"]`` (TOML) or ``"4"`` /
    ``"Fri,Mon"`` (env). All three encode the same Friday gate."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_SKIP_WEEKDAYS", "4")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from datetime import date

    session = date(2026, 5, 29)
    bars = _build_anchor_bars_with_width(session, target_width=73.25)
    bot = _MockBot(bars)
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    asyncio.run(strat.analyze("MNQ"))
    assert strat._state["MNQ"]["phase"] == "idle"


# ── Far-sweep guard (2026-05-29) ────────────────────────────────────────────


def test_far_sweep_guard_skips_fade_when_close_too_far_above_high(monkeypatch, caplog):
    """A close way above H (> max_sweep_distance_widths × width) must not emit
    a fade signal — the broker would reject the stop-entry as out-of-band and the
    geometry is "catch a falling knife from above" anyway.

    Replicates 2026-05-29 13:47 MGC: anchor 4556.80-4571.90 (W=15.10), price
    drifted to ~$43 above H, strategy fired SHORT @ H, broker returned
    ``Invalid price. Price is outside allowed range.`` (Code 2).
    """
    import logging

    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_REENTRY_CLOSE", "false")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REENTRY_THRESHOLD_POINTS", "0")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MIN_RANGE_WIDTH_POINTS", "0")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_RANGE_WIDTH_POINTS", "0")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_SWEEP_DISTANCE_WIDTHS", "2.0")

    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    cfg = _live_cfg()
    # 7:00 ET = 12:00 UTC. Anchor [100, 110] (W=10) on bars 0..11.
    # Far-sweep bar 12: close=140 → 30pt = 3.0× width past H. Should be skipped (cap=2.0).
    t0 = _utc(2026, 1, 7, 12, 0)
    bars = []
    for i in range(12):
        bars.append(
            {
                "timestamp": t0 + timedelta(minutes=5 * i),
                "open": 105, "high": 110, "low": 100, "close": 105, "volume": 1,
            }
        )
    bars.append(
        {
            "timestamp": t0 + timedelta(minutes=5 * 12),
            "open": 105, "high": 145, "low": 105, "close": 140, "volume": 1,
        }
    )

    bot = _MockBot(bars[:1])
    strat = MorningRangeReversionStrategy(bot, cfg)
    with caplog.at_level(logging.WARNING, logger="strategies.morning_range_reversion_strategy"):
        sig = None
        for k in range(1, len(bars) + 1):
            bot.bars = bars[:k]
            bot._current_bar_timestamp = bars[k - 1]["timestamp"]
            sig = asyncio.run(strat.analyze("MNQ"))
            if sig is not None:
                break

    assert sig is None, f"far-sweep must be skipped, got signal {sig}"
    assert any("sweep too far" in r.message for r in caplog.records), (
        "must emit the 🛰️  sweep too far WARNING with diagnostic context"
    )


def test_far_sweep_guard_allows_fade_when_close_within_cap(monkeypatch):
    """A close within the cap (≤ max_sweep_distance_widths × width past H) must
    still emit the fade — the guard is a *far-only* filter, not a no-sweep filter."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_REENTRY_CLOSE", "false")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REENTRY_THRESHOLD_POINTS", "0")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MIN_RANGE_WIDTH_POINTS", "0")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_RANGE_WIDTH_POINTS", "0")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_SWEEP_DISTANCE_WIDTHS", "2.0")

    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    cfg = _live_cfg()
    t0 = _utc(2026, 1, 7, 12, 0)
    bars = []
    for i in range(12):
        bars.append(
            {
                "timestamp": t0 + timedelta(minutes=5 * i),
                "open": 105, "high": 110, "low": 100, "close": 105, "volume": 1,
            }
        )
    # Sweep bar 12: close=112 → only 2pt past H on a 10pt range = 0.2× width. Comfortably inside the cap.
    bars.append(
        {
            "timestamp": t0 + timedelta(minutes=5 * 12),
            "open": 105, "high": 115, "low": 104, "close": 112, "volume": 1,
        }
    )

    bot = _MockBot(bars[:1])
    strat = MorningRangeReversionStrategy(bot, cfg)
    sig = None
    for k in range(1, len(bars) + 1):
        bot.bars = bars[:k]
        bot._current_bar_timestamp = bars[k - 1]["timestamp"]
        sig = asyncio.run(strat.analyze("MNQ"))
        if sig is not None:
            break

    assert sig is not None, "fade WITHIN the distance cap must still emit"
    assert sig["action"] == "SHORT"
    assert sig["entry_price"] == 110.0


def test_far_sweep_guard_disabled_when_set_to_zero(monkeypatch):
    """``max_sweep_distance_widths=0`` disables the guard entirely (legacy)."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_REENTRY_CLOSE", "false")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REENTRY_THRESHOLD_POINTS", "0")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MIN_RANGE_WIDTH_POINTS", "0")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_RANGE_WIDTH_POINTS", "0")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_SWEEP_DISTANCE_WIDTHS", "0")

    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    cfg = _live_cfg()
    t0 = _utc(2026, 1, 7, 12, 0)
    bars = []
    for i in range(12):
        bars.append(
            {
                "timestamp": t0 + timedelta(minutes=5 * i),
                "open": 105, "high": 110, "low": 100, "close": 105, "volume": 1,
            }
        )
    # Same far-sweep bar as the "skipped" test — but with cap=0 it must STILL emit.
    bars.append(
        {
            "timestamp": t0 + timedelta(minutes=5 * 12),
            "open": 105, "high": 145, "low": 105, "close": 140, "volume": 1,
        }
    )
    bot = _MockBot(bars[:1])
    strat = MorningRangeReversionStrategy(bot, cfg)
    sig = None
    for k in range(1, len(bars) + 1):
        bot.bars = bars[:k]
        bot._current_bar_timestamp = bars[k - 1]["timestamp"]
        sig = asyncio.run(strat.analyze("MNQ"))
        if sig is not None:
            break

    assert sig is not None, "guard=0 must restore legacy behaviour (emit even far-away)"


def test_far_sweep_guard_low_side(monkeypatch, caplog):
    """Symmetric guard on the low side: close way below L must also be skipped."""
    import logging

    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_REENTRY_CLOSE", "false")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REENTRY_THRESHOLD_POINTS", "0")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MIN_RANGE_WIDTH_POINTS", "0")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_RANGE_WIDTH_POINTS", "0")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_SWEEP_DISTANCE_WIDTHS", "2.0")

    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    cfg = _live_cfg()
    t0 = _utc(2026, 1, 7, 12, 0)
    bars = []
    for i in range(12):
        bars.append(
            {
                "timestamp": t0 + timedelta(minutes=5 * i),
                "open": 105, "high": 110, "low": 100, "close": 105, "volume": 1,
            }
        )
    # close=70 → 30pt below L on a 10pt range = 3.0× width — must be skipped.
    bars.append(
        {
            "timestamp": t0 + timedelta(minutes=5 * 12),
            "open": 105, "high": 105, "low": 65, "close": 70, "volume": 1,
        }
    )
    bot = _MockBot(bars[:1])
    strat = MorningRangeReversionStrategy(bot, cfg)
    with caplog.at_level(logging.WARNING, logger="strategies.morning_range_reversion_strategy"):
        sig = None
        for k in range(1, len(bars) + 1):
            bot.bars = bars[:k]
            bot._current_bar_timestamp = bars[k - 1]["timestamp"]
            sig = asyncio.run(strat.analyze("MNQ"))
            if sig is not None:
                break

    assert sig is None
    assert any("sweep too far" in r.message and "L=" in r.message for r in caplog.records), (
        "low-side message must reference L"
    )
