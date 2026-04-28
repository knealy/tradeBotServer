<!-- Last updated: 2026-04-28. Update on every operational change. -->

# TradeBotServer — Operations Playbook

Audience: on-call operator or LLM agent responding to a live incident.
All paths are relative to the repo root. All commands assume you are in the repo root and the virtualenv is active.

---

## Daily ops

### Start overnight_range on account 3 (canonical)

[scripts/run_overnight.sh](scripts/run_overnight.sh) wraps `caffeinate` + `core/strategy_executor.py` with the standard symbol set (MNQ, MES, MGC), 2-minute bars, and production risk limits. It writes a timestamped log to `logs/overnight_range_account3_<TS>_<PID>.log`.

```bash
bash scripts/run_overnight.sh 3
```

To run in the background and detach from the terminal:

```bash
nohup bash scripts/run_overnight.sh 3 >> logs/nohup_account3.out 2>&1 &
echo "PID: $!"
```

### Start a different strategy on account 1

Pass `--strategy` and `--account_select` directly to [core/strategy_executor.py](core/strategy_executor.py). Supported strategy names: `overnight_range`, `mean_reversion`, `trend_following`, `simple_candle`, `simple_momentum`, `trend_scalping`.

```bash
nohup caffeinate -dimsu python3 core/strategy_executor.py \
  --symbols=mnq,mes,mgc \
  --timeframe=2m \
  --strategy=mean_reversion \
  --account_select=1 \
  --risk-config '{"MNQ":{"max_quantity":2,"cooldown":60.0,"max_pending":2},"MES":{"max_quantity":2,"cooldown":60.0,"max_pending":2},"MGC":{"max_quantity":1,"cooldown":60.0,"max_pending":1}}' \
  >> logs/mean_reversion_account1.log 2>&1 &
echo "PID: $!"
```

### Stop everything safely

[scripts/stop_all.sh](scripts/stop_all.sh) kills the `trading` tmux session (if present) and then sends SIGTERM to any remaining `strategy_executor.py` and `trading_bot.py` processes. Verify nothing is left running after:

```bash
bash scripts/stop_all.sh
sleep 2
ps aux | rg "strategy_executor|trading_bot" | rg -v rg
```

A clean system returns no output from the final command.

### Flatten ALL positions immediately (panic button)

**Option 1 — via CLI (preferred while SignalR is up):**

```bash
python trading_bot.py
# then at the interactive prompt:
flatten
```

**Option 2 — direct REST call (use when SignalR is down or bot is unresponsive):**

Substitute `$ACCOUNT_ID` with the numeric account ID and `$TOKEN` with a valid JWT (from `.env` or a fresh login).

```bash
# Get open positions
curl -s -X POST https://api.topstepx.com/api/Position/searchOpen \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"accountId": '"$ACCOUNT_ID"'}' | python3 -m json.tool

# Close a single position by contract ID
curl -s -X POST https://api.topstepx.com/api/Position/closeContract \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"accountId": '"$ACCOUNT_ID"', "contractId": '"$CONTRACT_ID"'}' | python3 -m json.tool
```

Repeat the `closeContract` call for each open position returned by `searchOpen`.

### Restart a single strategy without dropping other accounts

Identify the PID of the target account's executor, kill it, then relaunch. Other accounts continue running in their own processes.

```bash
# Find the target PID
ps aux | rg "strategy_executor.*account_select=3"

# Kill that PID only
kill <PID>
sleep 3

# Verify it is gone
ps aux | rg "strategy_executor.*account_select=3" | rg -v rg

# Relaunch
nohup bash scripts/run_overnight.sh 3 >> logs/nohup_account3_restart.out 2>&1 &
echo "Restarted PID: $!"
```

### Switch accounts mid-run

There is no live account switch. The executor binds to `--account_select` at startup. To move to a different account: kill the current executor, then relaunch with the new account number.

```bash
# Kill existing executor (replace PID)
kill <OLD_PID>
sleep 2

# Relaunch on the new account
nohup bash scripts/run_overnight.sh <NEW_ACCOUNT_NUM> >> logs/nohup_account<NEW_ACCOUNT_NUM>.out 2>&1 &
```

---

## Logs & monitoring

### Tail the freshest strategy log

```bash
ls -t logs/*.log | head -1 | xargs tail -f
```

To tail all active strategy logs simultaneously:

```bash
tail -f $(ls -t logs/overnight_range_*.log | head -4)
```

### Switch logging to DEBUG temporarily

Edit `.env`, set `LOG_LEVEL=DEBUG`, then restart the relevant executor. The file handler level is set at process startup via `core/logging_setup.configure_logging()`; there is no live reload of `LOG_LEVEL`.

```bash
# Edit .env
sed -i '' 's/^LOG_LEVEL=.*/LOG_LEVEL=DEBUG/' .env
# Or add if missing:
echo 'LOG_LEVEL=DEBUG' >> .env

# Restart the executor (replace <ACCOUNT_NUM>)
kill $(ps aux | rg "strategy_executor.*account_select=<ACCOUNT_NUM>" | rg -v rg | awk '{print $2}')
sleep 2
nohup bash scripts/run_overnight.sh <ACCOUNT_NUM> >> logs/nohup_debug.out 2>&1 &
```

Revert after debugging:

```bash
sed -i '' 's/^LOG_LEVEL=DEBUG/LOG_LEVEL=INFO/' .env
```

### Inspect Postgres cache hit rate

```sql
-- Cache hit rate from api_metrics (last 24 hours)
SELECT
    endpoint,
    COUNT(*) AS total_calls,
    SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END) AS cache_hits,
    ROUND(100.0 * SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END) / COUNT(*), 2) AS hit_rate_pct,
    ROUND(AVG(response_time_ms)::numeric, 2) AS avg_ms
FROM api_metrics
WHERE recorded_at > NOW() - INTERVAL '24 hours'
GROUP BY endpoint
ORDER BY total_calls DESC;

-- Cache metadata freshness
SELECT key, hit_count, miss_count, last_accessed, expires_at
FROM cache_metadata
ORDER BY last_accessed DESC
LIMIT 20;
```

### Inspect last 10 trades

```sql
SELECT
    th.id,
    th.account_id,
    th.symbol,
    th.side,
    th.quantity,
    th.entry_price,
    th.exit_price,
    th.pnl,
    th.created_at,
    se.strategy_name,
    se.signal_reason
FROM trade_history th
LEFT JOIN strategy_executions se ON se.trade_id = th.id
ORDER BY th.created_at DESC
LIMIT 10;
```

### Check Rust toggle status

```bash
bash scripts/toggle_rust.sh status
```

To enable or disable:

```bash
bash scripts/toggle_rust.sh on    # sets TOPSTEPX_USE_RUST=true in .env
bash scripts/toggle_rust.sh off   # sets TOPSTEPX_USE_RUST=false in .env
```

A `.env.backup.<timestamp>` is written automatically before any change. The toggle only rewrites `.env`; restart running executors for the change to take effect.

---

## Triage flowcharts

### "No signals firing for hours"

**Symptoms:** Strategy log shows bar updates but no order attempts; `strategy_executions` table has no recent rows.

**Check first:**
1. Confirm executor is alive: `ps aux | rg strategy_executor | rg -v rg`
2. Search the active log for SignalR connection events: `rg "market hub|hub connected|Subscribed" logs/*.log | tail -20`
3. Check bar aggregator is receiving ticks: `rg "bar_aggregator|Broadcasting bar|bar update" logs/*.log | tail -20`

**Root causes:**
- Executor process silently exited (OOM, unhandled exception). Fix: relaunch via `run_overnight.sh`.
- SignalR market hub disconnected and backoff stalled. Fix: restart executor; the hub reconnects on startup.
- `strategy.is_running` flag was set to `False` in `strategy_states` table (e.g., from a previous stop command). Fix: `UPDATE strategy_states SET is_running = true WHERE strategy_name = 'overnight_range';` then restart.
- Market is closed (holiday or outside session hours). Check session schedule before escalating.

```bash
# Quick process check
ps aux | rg "strategy_executor" | rg -v rg

# Check is_running flag in DB
psql "$DATABASE_URL" -c "SELECT strategy_name, is_running, updated_at FROM strategy_states ORDER BY updated_at DESC LIMIT 10;"

# Search log for hub connection
rg "market hub|hub connected|Subscribed|bar_aggregator" $(ls -t logs/*.log | head -1) | tail -30
```

### "Duplicate orders"

**Symptoms:** Two or more identical orders placed within seconds; `order_history_cache` shows repeated entries for the same signal.

**Check first:**
1. Verify `max_pending` in the active risk config (default: 2 for MNQ/MES, 1 for MGC).
2. Check cooldown setting: the `--risk-config` JSON passed at startup or the TOML at `config/strategies/overnight_range.toml`.
3. Search the order audit log: `rg "DUPLICATE|cooldown|pending" $(ls -t logs/*.log | head -1) | tail -30`

**Root causes:**
- `max_pending` set too high; lower it in `--risk-config` JSON or TOML and restart.
- Cooldown period shorter than order fill latency. Increase `cooldown` to 90–120 s.
- Two executor instances started for the same account. Fix: run `stop_all.sh` then relaunch once.
- Event bus publishing the same signal twice due to a reconnect flush. Check `rg "EventType.SIGNAL" logs/*.log | tail -20`.

```bash
# Check for multiple executor instances on same account
ps aux | rg "strategy_executor" | rg -v rg

# Inspect order audit
rg "DUPLICATE|cooldown|max_pending|pending orders" $(ls -t logs/*.log | head -1) | tail -30
```

### "DLL breach false positive"

**Symptoms:** Bot stops trading and logs a daily-loss-limit breach, but account P&L appears within limits.

**Check first:**
1. Inspect `account_tracker` in the log: `rg "daily_pnl|DLL|daily loss|account_tracker" $(ls -t logs/*.log | head -1) | tail -30`
2. Check recent fills: `rg "fill|filled|position closed" $(ls -t logs/*.log | head -1) | tail -30`
3. Verify daily reset timing: DLL resets at 5:00 PM CT (broker daily settlement). If bot restarted across a session boundary, `account_tracker` may have double-counted.

**Root causes:**
- Bot was restarted mid-session and re-added existing P&L on top of the carried-over state.
- Stale `account_state` row in Postgres reflects an old balance. Fix: `UPDATE account_state SET daily_pnl = 0, updated_at = NOW() WHERE account_id = '<ID>';` and restart.
- `daily_reset_time` env var not aligned with broker's daily reset. Confirm `DAILY_RESET_HOUR` in `.env`.

```bash
psql "$DATABASE_URL" -c "SELECT account_id, daily_pnl, balance, updated_at FROM account_state ORDER BY updated_at DESC LIMIT 5;"
```

### "SignalR disconnect storm"

**Symptoms:** Log floods with reconnect attempts; market data gaps; orders not placed.

**Check first:**
1. Confirm exponential backoff is active: `rg "backoff|reconnect|attempt" $(ls -t logs/*.log | head -1) | tail -20`
2. Check network health from the host: `curl -s -o /dev/null -w "%{http_code}" https://rtc.topstepx.com`
3. Verify JWT is not expired: `rg "token expired|401|JWT" $(ls -t logs/*.log | head -1) | tail -20`

**Root causes:**
- Stale JWT. The hub rejects the WebSocket upgrade with 401. Fix: rotate JWT (see "Rotate JWT" in Backtest & dev section) and restart.
- TopStepX RTC server maintenance or outage. Check [status.topstepx.com](https://status.topstepx.com) and wait.
- Hub URL changed. Verify `PROJECT_X_MARKET_HUB_URL` in `.env` matches the current endpoint.
- High reconnect frequency causing rate-limit. The backoff in `core/websocket_manager.py` caps retries; if flooded, restart the executor to reset backoff state.

```bash
# Count reconnects in last 100 log lines
rg "reconnect|disconnect|hub" $(ls -t logs/*.log | head -1) | tail -100 | wc -l

# Check JWT expiry (decode without verifying)
python3 -c "
import os, base64, json
t = os.getenv('JWT_TOKEN','').split('.')
if len(t)>=2:
    p = t[1] + '=='
    print(json.loads(base64.urlsafe_b64decode(p)).get('exp'))
"
```

### "Discord stopped notifying"

**Symptoms:** No Discord messages for fills or errors that would normally trigger notifications.

**Check first:**
1. Confirm `DISCORD_WEBHOOK_URL` is set: `grep DISCORD_WEBHOOK_URL .env`
2. Search log for notifier errors: `rg "discord|webhook|rate.limit" trading_bot.log | tail -20`
3. Test the webhook manually with curl.

**Root causes:**
- `DISCORD_WEBHOOK_URL` unset or empty. Set it in `.env` and restart.
- Discord rate-limited the webhook (429). `DiscordNotifier` drops messages when the internal 0.5 s guard fires. Wait 60 s; the next notification will go through.
- Webhook URL was deleted or rotated on the Discord side. Generate a new one in Discord → Channel Settings → Integrations → Webhooks.

```bash
grep 'DISCORD_WEBHOOK_URL' .env

# Manual test
WEBHOOK=$(grep DISCORD_WEBHOOK_URL .env | cut -d= -f2-)
curl -s -X POST "$WEBHOOK" \
  -H "Content-Type: application/json" \
  -d '{"content":"playbook test ping"}'
```

### "Executor zombie process"

**Symptoms:** `ps aux` shows `strategy_executor.py` but the log has no recent output; `caffeinate` is also stuck.

**Check first:**
1. Check last log timestamp: `tail -5 $(ls -t logs/*.log | head -1)`
2. Confirm process is truly hung (not just quiet during off-hours): `ls -lt logs/*.log | head -3`
3. Run `stop_all.sh` to kill the entire tree.

**Fix:**

```bash
bash scripts/stop_all.sh
sleep 3
ps aux | rg "strategy_executor|caffeinate" | rg -v rg

# If still present, force kill
pkill -9 -f strategy_executor.py
pkill -9 -f "caffeinate.*python"

# Relaunch
nohup bash scripts/run_overnight.sh 3 >> logs/nohup_account3.out 2>&1 &
```

### "Config TOML reload not picked up"

**Symptoms:** Changes to `config/strategies/<name>.toml` have no effect on running strategy behavior.

**Check first:**
1. Confirm the strategy loop calls `cfg.maybe_reload()`. Grep the strategy source: `rg "maybe_reload" core/`.
2. Check file mtime is actually newer: `stat config/strategies/overnight_range.toml`
3. Check the log for a "Reloaded strategy config" line after your edit.

**Root causes:**
- `maybe_reload()` is not being called in the strategy's tick loop (it may have been removed during a refactor). Fix: add the call and restart.
- File was edited but mtime is not updated (e.g., some editors write to a temp file then rename — mtime will update; but some NFS mounts cache mtime). Fix: `touch config/strategies/overnight_range.toml` to force mtime update.
- The config object was created before the TOML file existed and `path` is `None`. Fix: restart the executor.

```bash
stat config/strategies/overnight_range.toml
rg "Reloaded strategy config" $(ls -t logs/*.log | head -1) | tail -5
```

### "Order placement returns 500"

**Symptoms:** Log shows `500` responses from `/api/Order/place`; no orders go through.

**Check first:**
1. Check JWT expiry (see SignalR section above). A 401 upstream can manifest as 500 from the adapter.
2. Verify `--account_select` maps to a valid account ID: `rg "account_select|account_id|selected account" $(ls -t logs/*.log | head -1) | tail -10`
3. Check for rate-limit messages: `rg "429|rate.limit|too many" $(ls -t logs/*.log | head -1) | tail -10`

**Root causes:**
- Expired or missing JWT. Rotate manually (see below) and restart.
- Wrong account ID passed. The adapter at [brokers/topstepx_adapter.py](brokers/topstepx_adapter.py) (around line 412) logs the full order payload just before the API call; read it to confirm `accountId` is correct.
- Malformed order payload (tick size violation, invalid contract ID). Fix: check the logged payload, correct symbol mapping in `core/market_data.py`.
- Broker API is down. Verify with `curl -s -o /dev/null -w "%{http_code}" https://api.topstepx.com/api/Auth/loginKey -X POST -H "Content-Type: application/json" -d '{}'`.

```bash
# Read the last 500 error context from log
rg -B 5 -A 5 "500|Internal Server Error" $(ls -t logs/*.log | head -1) | tail -50
```

---

## Backtest & dev

### Replay historical bars through a strategy

```bash
python core/backtest_executor.py \
  --strategy=overnight_range \
  --symbol=MNQ \
  --timeframe=5m \
  --start=2025-01-01 \
  --end=2025-03-31

# With Monte Carlo simulation
python core/backtest_executor.py \
  --strategy=overnight_range \
  --symbol=MNQ \
  --timeframe=5m \
  --days=90 \
  --monte-carlo=1000

# Full help
python core/backtest_executor.py --help
```

### Rotate JWT manually if env-loaded copy is stale

The `AuthManager` in [core/auth.py](core/auth.py) auto-refreshes using `PROJECT_X_API_KEY` + `PROJECT_X_USERNAME`. If the env-preloaded `JWT_TOKEN` is stale and auto-refresh is failing, force a fresh login and update `.env`:

```bash
# Trigger a fresh login via the CLI
python trading_bot.py
# At the prompt:
login

# Alternatively, hit the auth endpoint directly and capture the token
NEW_TOKEN=$(curl -s -X POST https://api.topstepx.com/api/Auth/loginKey \
  -H "Content-Type: application/json" \
  -d "{\"userName\":\"$(grep PROJECT_X_USERNAME .env | cut -d= -f2-)\",\"apiKey\":\"$(grep PROJECT_X_API_KEY .env | cut -d= -f2-)\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))")

echo "Token: ${NEW_TOKEN:0:40}..."

# Write to .env
sed -i '' "s/^JWT_TOKEN=.*/JWT_TOKEN=${NEW_TOKEN}/" .env
# Or if not present:
echo "JWT_TOKEN=${NEW_TOKEN}" >> .env
```

After updating `.env`, restart all executors for the new token to take effect.

---

## Deploy

### Railway deployment

Railway builds from the [Dockerfile](Dockerfile). A `git push` to the tracked branch triggers a rebuild and rolling restart. The `JWT_TOKEN` and all secrets are set as Railway environment variables, not in the repo.

```bash
git add -A && git commit -m "deploy: <description>"
git push origin main

# Stream Railway logs after push
railway logs --tail
```

### Local Docker test

```bash
docker build -t tradebot .

# Run with local .env
docker run --env-file .env -p 8080:8080 tradebot

# Run interactively for debugging
docker run --env-file .env -p 8080:8080 -it tradebot bash
```

### Start webhook + dashboard server locally

```bash
python servers/start_async_webhook.py
```

The async webhook server binds to port 8080 by default (configurable via `WEBHOOK_PORT` in `.env`). The dashboard HTML is served from `gui/master_control.html`.

### Multi-account orchestration

```bash
# Start instances for accounts 1, 2, 3 (creates tmux session multi_account_trading)
bash scripts/start_multi_account.sh 1 2 3

# Attach to the tmux session to monitor
tmux attach -t multi_account_trading

# Stop all instances
bash scripts/stop_multi_account.sh
```
