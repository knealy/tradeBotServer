# Discord Channels — Implementation Guide

**Status:** Ops playbook (2026-07-07)  
**Prerequisites:** `.env` with `DISCORD_WEBHOOK_URL`; optional `DISCORD_BOT_TOKEN` for slash commands

This guide maps each proposed channel to **concrete wiring** in this repo. You already have webhooks + bot token env vars in `.env.example`.

---

## Architecture overview

```
┌─────────────────────────────────────────────────────────────┐
│  Webhook path (simple, one-way)                              │
│  DISCORD_WEBHOOK_URL → core/discord_notifier.py             │
│  Used by: executor, wrapper ping, status digest, alerts      │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  Bot gateway path (two-way, slash commands)                  │
│  DISCORD_BOT_TOKEN + DISCORD_COMMAND_CHANNEL_ID             │
│  → servers/discord_command_bot.py (to be added)             │
│  → HTTP to local trading_bot / strategy_executor            │
└─────────────────────────────────────────────────────────────┘
```

**Recommendation:** Use **separate Discord channels** with **channel-specific webhooks** (create one webhook per channel in Discord server settings). Set env vars per deployment or use a small router in Python.

---

## Step 0 — Create channels and webhooks

In your Discord server:

1. Create channels (suggested names below).
2. Per channel: **Edit Channel → Integrations → Webhooks → New Webhook** → copy URL.
3. Add to `.env` (never commit):

```bash
# Primary (fills, status, wrapper — can stay as default)
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."

# Optional dedicated webhooks (add as you split channels)
DISCORD_WEBHOOK_OPS="https://discord.com/api/webhooks/..."
DISCORD_WEBHOOK_RECAP="https://discord.com/api/webhooks/..."
DISCORD_WEBHOOK_RISK="https://discord.com/api/webhooks/..."
DISCORD_WEBHOOK_REGIME="https://discord.com/api/webhooks/..."
DISCORD_WEBHOOK_WALKFORWARD="https://discord.com/api/webhooks/..."
```

---

## Channel-by-channel implementation

### #ops-health (executor up/down, missed sessions)

**Already partially done:**

| Event | Source | Status |
|-------|--------|--------|
| Wrapper scheduled | `scripts/discord_wrapper_ping.py` → `scheduled` | ✅ |
| Executor start | `executor_start` ping | ✅ |
| Session end / failure | `session_end`, `failed`, `idle_exit` | ✅ |
| Hourly heartbeat | `trading_bot._discord_status_reporter_loop` | ✅ when executor runs |

**Add — missed launchd detector (cron or launchd):**

Create `scripts/check_mrr_launchd_health.sh`:

```bash
#!/usr/bin/env bash
# Run daily at 17:00 ET via cron — alerts if no executor log today (weekday).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/.env" 2>/dev/null || true
TODAY=$(date +%Y%m%d)
LOG=$(ls -t "$ROOT/logs/morning_range_reversion_account"*_${TODAY}_*.log 2>/dev/null | head -1)
if [ -z "$LOG" ] && [ "$(date +%u)" -le 5 ]; then
  "$ROOT/.venv/bin/python" "$ROOT/scripts/discord_wrapper_ping.py" missed_session \
    "No MRR executor log for $TODAY — check launchd / Mac sleep"
fi
```

Cron example (host local time ~5 PM):

```cron
0 17 * * 1-5 cd /Users/risu/tradeBotServer && bash scripts/check_mrr_launchd_health.sh
```

**Env:** Point `DISCORD_WEBHOOK_URL` at `#ops-health` webhook, or teach `discord_wrapper_ping.py` to read `DISCORD_WEBHOOK_OPS`.

---

### #session-recap (EOD PnL, trades, WR)

**Existing:** `core/discord_status_digest.py` daily section via `DISCORD_STATUS_INCLUDE_DAILY=1`.

**Wire EOD-only post at session end:**

1. In `core/strategy_executor.py` shutdown path (after strategies stop), call new helper:

```python
# Pseudocode — hook in executor finally block
await send_session_recap_discord(trading_bot)
```

2. Implement `send_session_recap_discord` in `core/discord_status_digest.py`:
   - Read `session_trade_tracker.get_session_pnl(account_id)`
   - Lines: trades, net PnL, WR, symbols traded, regime label if publisher active
   - Use `DISCORD_WEBHOOK_RECAP` or default webhook

**Env:**

```bash
DISCORD_STATUS_INCLUDE_DAILY=1
DISCORD_WEBHOOK_RECAP="..."   # optional dedicated channel
```

---

### #risk-alerts (DLL, breaker, regime sizing block)

**Hook points:**

| Alert | File | Trigger |
|-------|------|---------|
| Regime sizing block | `strategies/strategy_base.py` | Already logs; add `discord_notifier.send_error_notification` when block |
| Consec-loss breaker | `core/consec_loss_breaker.py` | On cooldown start |
| DLL proximity | `core/risk_management.py` | When `respect_dll` threshold approached |
| Feed down | `DataFeedWatchdog` | ✅ already via `DATA_FEED_DISCORD_ALERTS` |

**Minimal patch pattern** (regime block example in `strategy_base.py`):

```python
notifier = getattr(bot, "discord_notifier", None)
if notifier and notifier.enabled:
    asyncio.create_task(notifier.send_error_notification(
        f"{self.config.name} {symbol}: regime sizing blocked entry",
        context=msg,
    ))
```

Use **`DISCORD_WEBHOOK_RISK`** via new env + optional url arg on notifier.

---

### #walkforward (nightly sweep summary)

**After walk-forward completes:**

Add to end of `scripts/walkforward_trade_recap_report.py`:

```python
from scripts.discord_wrapper_ping import send_wrapper_ping
send_wrapper_ping(
    "walkforward_done",
    f"dir={out_dir} ret={tot_ret}% dd={max_dd}% trades={n}",
)
```

Or post embed with link to `file:///.../metrics.html` (Discord won't auto-link local files — paste Railway/public URL if hosted).

**Schedule:**

```cron
0 2 * * 0 cd /Users/risu/tradeBotServer && ENABLE_SIGNALR=false .venv/bin/python scripts/walkforward_trade_recap_report.py --days 90 --folds 3 ...
```

---

### #chart-snapshots (recap PNG on fill)

**Path:**

1. On `TRADE_CLOSED` event in `core/user_hub_handlers.py` or fill handler:
2. Call `gui/chart_html.py` trade recap export (headless) → PNG if playwright/selenium available, else HTML link.
3. Discord webhook **multipart** upload:

```python
# discord_notifier.py — add method
async def send_file_notification(self, content: str, file_path: Path) -> bool:
    # POST multipart/form-data with payload_json + file
```

**Complexity:** High (browser render). **MVP:** Post trade recap **URL** from master GUI if running, or text summary only.

---

### #commands (`/status`, `/flatten`, `/pause`)

You have in `.env`:

```bash
DISCORD_BOT_TOKEN=""
DISCORD_COMMAND_CHANNEL_ID=""
DISCORD_COMMAND_ALLOWED_USER_IDS=""
```

**Implementation sketch** (`servers/discord_command_bot.py`):

1. Use `discord.py` or raw gateway with **Application Commands**.
2. Register slash commands: `/status`, `/flatten [symbol]`, `/pause [strategy]`.
3. Verify `interaction.user.id` in `DISCORD_COMMAND_ALLOWED_USER_IDS`.
4. `/status` → HTTP GET `http://127.0.0.1:8080/api/health` (extend webhook server) or read latest log mtime.
5. `/flatten` → POST to local admin endpoint that calls `TopStepXTradingBot.flatten_all()`.
6. Run bot as **separate launchd agent** (always on, low CPU) — not inside strategy executor.

**Security:**

- Never expose admin HTTP to `0.0.0.0`.
- Bind `127.0.0.1` only; bot process on same Mac.
- Rate-limit commands (1 per 30s per user).

---

### #calendar (holidays, FOMC/CPI)

**Existing:** `core/market_calendar.equity_futures_session_note` used in daily Discord briefing.

**Extend:**

1. Add `config/macro_calendar.toml` with FOMC/CPI dates (manual update quarterly).
2. Morning wrapper ping appends today's macro flags.
3. Optional: half-size or no-trade on `high_impact` days via TOML + strategy config.

**Discord-only MVP:** Include in `DISCORD_STATUS_INCLUDE_DAILY` lines:

```python
if cal.get("macro_event"):
    lines.append(f"macro: {cal['macro_event']}")
```

---

### #regime (label changes with KER/ADX)

**Wire when publisher enabled:**

```python
# trading_bot.py or new core/discord_regime_subscriber.py
async def on_regime_update(event):
    data = event.data
    if data.get("previous_label") == data.get("label"):
        return
    await discord_notifier.send_status_digest(
        "Regime change",
        [
            f"{data['symbol']} {data['timeframe']}: "
            f"{data.get('previous_label')} → {data['label']}",
            f"KER={data['ker']:.2f} ADX={data['adx']:.1f} vol_pct={data['vol_pct']:.2f}",
        ],
    )
```

**Env:**

```bash
REGIME_PUBLISHER_ENABLED=1
DISCORD_WEBHOOK_REGIME="..."
```

Subscribe in `ensure_regime_publisher` after start.

---

### #multi-account

**When running account 1 + 2:**

Loop accounts in `build_discord_status_lines`:

```python
for acc in bot.all_accounts or [bot.selected_account]:
    lines.append(f"--- account {acc['name']} ---")
    # positions, session_pnl per account
```

One embed per account → sequential webhook messages to `#multi-account`.

---

## Env reference (add to `.env.example` when split)

| Variable | Purpose |
|----------|---------|
| `DISCORD_WEBHOOK_URL` | Default webhook |
| `DISCORD_WEBHOOK_OPS` | #ops-health |
| `DISCORD_WEBHOOK_RECAP` | #session-recap |
| `DISCORD_WEBHOOK_RISK` | #risk-alerts |
| `DISCORD_WEBHOOK_REGIME` | #regime |
| `DISCORD_WRAPPER_PING` | `1` = wrapper lifecycle pings |
| `DISCORD_STATUS_INTERVAL_SECONDS` | Heartbeat interval |
| `DISCORD_BOT_TOKEN` | Slash command bot |
| `DISCORD_COMMAND_CHANNEL_ID` | Restrict commands channel |
| `DISCORD_COMMAND_ALLOWED_USER_IDS` | Comma-separated Discord user IDs |

---

## Suggested rollout order

1. ✅ **#ops-health** — wrapper pings (done) + missed-session cron (script above).  
2. **#regime** — publisher + subscriber (1 file, ~40 lines).  
3. **#risk-alerts** — regime block + breaker notifications.  
4. **#session-recap** — executor shutdown hook.  
5. **#commands** — separate bot, highest effort.  
6. **#chart-snapshots** — defer until headless chart export exists.

---

## Testing

```bash
# Wrapper ping smoke test
source .env
.venv/bin/python scripts/discord_wrapper_ping.py test "hello from ops"

# Status digest (requires running bot or mock)
DISCORD_STATUS_INTERVAL_SECONDS=60  # shorten for test
bash scripts/run_morning_reversion.sh 1 --now
```

---

## Related docs

- `scripts/launchd/README.md` — launchd troubleshooting  
- `core/discord_notifier.py` — webhook implementation  
- `core/discord_status_digest.py` — heartbeat + daily briefing  
- `docs/regime/REGIME_DETECTION_WHITEPAPER.md` — what regime labels mean
