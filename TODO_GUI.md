
### GUI DESIGN:add section to GUI for backtesting / simulations + new widgets ?
add calendar view for metrics performance ?
plans for drawing tools / custom indicators ?

current price bar doesnt render properly as it forms 
- renders all candles up to the current one perfectly but 
when switching timeframes the current candle does not form properly because it only gets the new signalR price data 

order/position pill are such that they still show even when the order price is not visible 

need a button that refocuses chart on the current price + some # bars visible 

## Discord channel ideas (beyond current alerts)

You already have: hourly status digest, daily briefing, fills/closes, feed-down alerts, signal tiers. High-value additions:

| Channel / bot | What to post | How |
|---------------|--------------|-----|
| **#ops-health** | Executor up/down, missed launchd session, heartbeat age | Wrapper posts on start/exit; cron compares log mtime |
| **#session-recap** | EOD PnL, trades, win rate, regime label at close | Extend `discord_status_digest` with `DISCORD_STATUS_INCLUDE_DAILY=1` + session close hook |
| **#risk-alerts** | DLL proximity, consec-loss breaker trip, regime sizing block | Wire `StrategyRiskManager` + `regime_sizing` blocks to `DiscordNotifier` |
| **#walkforward** | Nightly/weekly sweep summary when new `metrics.html` lands | CI hook on `docs/perf/` |
| **#chart-snapshots** | Trade recap PNG or range-build screenshot on fill | `chart_html` export + webhook attachment |
| **#commands** (you have token/channel IDs) | `/status`, `/flatten`, `/pause MRR` slash commands | Discord bot gateway → local webhook server |
| **#calendar** | Holiday/half-day warnings, FOMC/CPI days | `equity_futures_session_note` + manual macro calendar |
| **#regime** | Regime label changes (chop↔mixed↔trend) with KER/ADX | Subscribe to `REGIME_UPDATE` when publisher enabled |
| **#multi-account** | Per-account rollup if you run account 1 + 2 | One digest embed per account_id |

**Implementation guide:** [`docs/DISCORD_CHANNELS_SETUP.md`](docs/DISCORD_CHANNELS_SETUP.md)  
**Done:** #ops-health wrapper pings + `scripts/check_mrr_launchd_health.sh`

---
