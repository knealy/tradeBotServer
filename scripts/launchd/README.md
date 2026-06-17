# MRR daily launchd agent

Daily-scheduled launcher for the `morning_range_reversion` strategy on macOS,
introduced 2026-06-15 as the recommended replacement for the "leave
`scripts/run_morning_reversion.sh` running all week" workflow.

## Why daily-scheduled

MRR is structurally a daily strategy: range build 7-8 AM ET, fade window
8 AM - 4 PM ET, flat by 16:00, no overnight state. Running the bot 24/7
for a ~9-hour daily strategy means 15h/day of zero-value process lifetime
and 15h of opportunities for SignalR drift, GC pressure, FD growth, and
state corruption. Daily-launchd gives us:

- **Bounded resource lifetime** — every day starts with a fresh process.
- **No weekend gap** — the process doesn't exist Friday night → Sunday evening.
- **Natural one-log-per-day** — each launch makes its own timestamped log.
- **Kernel-managed scheduling** — launchd's `StartCalendarInterval` is more
  reliable than userspace bash wait+sleep.
- **Simpler mental model** — "the OS starts a thing each morning; the thing
  exits in the afternoon".

What we keep from the always-on wrapper hardening (Round 1 + Round 2):

- Supervise loop with backoff + max-restart cap (in-day crash recovery).
- Hang watchdog (catches event-loop wedges between 7 AM and 4 PM).
- Startup reconciliation against broker state (adopts orphaned positions /
  cancels yesterday's unfilled working orders).
- Truncated-tag bug fix (`_live_tag_is_entry` now actually matches MRR's
  own orders in production).
- Daily log rotation (still useful if the wrapper's lifetime spans midnight).

## Install

```bash
bash scripts/install_mrr_launchd.sh <account_num>
```

The installer:

1. Reads `start_time` + `session_timezone` from
   `config/strategies/morning_range_reversion.toml`.
2. Computes "start_time minus 5 minutes" in ET, translates to the host's
   local clock.
3. Renders the plist template at
   `scripts/launchd/com.tradebot.mrr.plist.template`, validates with
   `plistlib`, copies to `~/Library/LaunchAgents/`, and runs
   `launchctl bootstrap`.

Tunables:

```bash
bash scripts/install_mrr_launchd.sh 1 --wake-lead 10   # 10 min before start_time
bash scripts/install_mrr_launchd.sh 1 --dry-run        # print without installing
bash scripts/install_mrr_launchd.sh 1 --reload         # unload + reinstall (after TOML edit)
```

## Verify

```bash
launchctl list | grep com.tradebot.mrr.account1
launchctl print "gui/$(id -u)/com.tradebot.mrr.account1"
```

Force a test run NOW (without waiting until tomorrow morning):

```bash
launchctl kickstart -k "gui/$(id -u)/com.tradebot.mrr.account1"
```

The wrapper will see it's outside the trading window, sleep until the next
scheduled wake-up, and the launchd-fired execution will exit. If you want
to actually trade RIGHT NOW (for testing only), bypass launchd:

```bash
bash scripts/run_morning_reversion.sh 1 --now
```

## Tail logs

```bash
# Wrapper stdout / stderr captured by launchd:
tail -f logs/launchd_mrr_account1.out
tail -f logs/launchd_mrr_account1.err

# Strategy executor's own log (one per launch, timestamped):
tail -f logs/morning_range_reversion_account1_*.log
```

## Uninstall

```bash
bash scripts/uninstall_mrr_launchd.sh <account_num>
```

`--keep` leaves the plist on disk if you want to inspect or re-load later.

## Daily lifecycle (what happens each morning)

```
06:50 ET   launchd fires → bash scripts/run_morning_reversion.sh 1
06:50 ET   wrapper reads TOML, computes today's schedule
06:50 ET   wrapper enters short countdown (already inside the wake window)
06:55 ET   wrapper exec's strategy_executor inside supervise loop
06:55 ET   executor auths to TopStepX, connects SignalR, subscribes symbols
07:00 ET   strategy starts building anchor range
08:00 ET   anchor range finalized, fade window opens
…trade…
16:00 ET   strategy flattens any open positions (flat_before)
16:30 ET   session-end timer SIGTERMs the executor (flat_before + 30min grace)
16:30 ET   supervise loop sees we're past cutoff → wrapper exits 0
16:30 ET   launchd sees the agent exited; waits until tomorrow 06:50 ET
```

If the executor crashes between 06:55 and 16:30, the supervise loop respawns
it with exponential backoff (5s → 10 → 20 → 40 → 80 → 160 → 300, capped).
If 10 fast crashes happen in a row, the wrapper bails out so an operator
can investigate.

## Tunables (env vars)

Set before invoking `install_mrr_launchd.sh` to bake into the wrapper environment, or set per-launch by editing the plist's `EnvironmentVariables` dict:

| Var | Default | What it does |
|---|---|---|
| `SESSION_EXIT_GRACE_MIN` | 30 | Minutes after flat_before before the wrapper exits |
| `MAX_RESTARTS` | 10 | Crash respawns before the wrapper bails |
| `RESET_AFTER_SEC` | 3600 | Healthy uptime that resets the restart counter |
| `BACKOFF_INITIAL` | 5 | First-restart backoff (seconds) |
| `BACKOFF_MAX` | 300 | Backoff cap (seconds) |
| `HANG_THRESHOLD_SEC` | 300 | Heartbeat staleness before hang watchdog SIGTERMs |
| `HANG_CHECK_INTERVAL_SEC` | 60 | How often the hang watchdog polls |
| `SIGNALR_MAX_RECONNECT_ATTEMPTS` | 1000 | Hard cap on reconnect tries |
| `SIGNALR_EXTENDED_RECONNECT_DELAY_SEC` | 300 | Slow-poll cadence after fast phase |
| `LOG_ROTATE_DAILY` | 1 (in wrapper) | Use `TimedRotatingFileHandler` at midnight |
| `STRATEGY_EXECUTOR_GUI_WS` | 0 (in wrapper) | Headless executor does not connect to GUI WebSocket |
| `DATA_FEED_CANCEL_ON_STALENESS` | false (in wrapper) | Do not cancel brackets on SignalR zombie reconnect |
| `DISCORD_STATUS_INTERVAL_SECONDS` | 1800 (in wrapper) | Periodic Discord status digest (0 = off) |
| `DATA_FEED_DISCORD_ALERTS` | true (in wrapper) | Discord alert when feed goes zombie / recovers |
| `DATA_FEED_DISCORD_ALERT_COOLDOWN_S` | 900 (in wrapper) | Min seconds between repeated feed-down alerts |
| `LOG_SUPPRESS_ASYNCIO_SESSION_ERRORS` | 1 (in wrapper) | Silence asyncio "Unclosed client session" spam |
| `LOG_MAX_BYTES` | 52428800 (in wrapper) | Size-rotation cap when not using daily rotation |
| `DATA_FEED_HEALTH_GATE_MODE` | warn (in wrapper) | Tiered placement gate (severe 15m silence refuses) |

## Code updates vs launchd reinstall

The plist only stores the **wrapper script path** and **schedule** — not a
copy of the Python code.  Editing strategy code, TOML, or
``run_morning_reversion.sh`` is picked up on the **next executor launch**
(tomorrow's calendar fire, or ``launchctl kickstart -k`` for an immediate
test).  You only need ``bash scripts/install_mrr_launchd.sh N --reload``
when the **schedule** changes (TOML ``start_time`` / wake lead) or you move
the repo path.

## GUI master + launchd MRR

Do **not** rely on `python trading_bot.py --command='master'` running
overnight while launchd MRR is active unless you understand the coupling:

- Master writes `.gui_websocket_port` in the repo root.
- Headless `strategy_executor` (when `STRATEGY_EXECUTOR_GUI_WS` is enabled)
  connects to that port to broadcast chart signals.
- A stale port file, a closed master, or the pre-2026-06-17 recursive
  keepalive bug can spawn hundreds of reconnect attempts and leak aiohttp
  sessions — starving the asyncio loop around the same time SignalR dies.

The MRR wrapper sets `STRATEGY_EXECUTOR_GUI_WS=0` so launchd runs are
decoupled from any GUI session. If you want charts while MRR trades, open
master **after** MRR is running and leave GUI WS disabled on the executor
(or run master from a different checkout).

## When NOT to use this

If you later add a strategy that:

- Holds overnight positions (covering Globex / Asia / European sessions)
- Tracks 24-hour rolling state across midnight
- Trades multiple sessions per day with state continuity (e.g.
  `overnight_range` builds a 6 PM – 9:29 AM range spanning midnight)

…the always-on daemon is the right model for THAT strategy. You can still
keep MRR on daily-launchd and run the always-on strategy separately.
