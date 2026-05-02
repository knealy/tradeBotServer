# Dashboard and webhook

## Production stack

- **Entry:** [servers/start_async_webhook.py](../servers/start_async_webhook.py) → [servers/async_webhook_server.py](../servers/async_webhook_server.py).
- **Static UI:** pre-built assets under `static/dashboard/` (served by aiohttp).
- **Legacy / dev chart server:** [gui/chart_html.py](../gui/chart_html.py), [gui/master_control.html](../gui/master_control.html) — see [gui/README.md](../gui/README.md) if present.

## Master control (unified chart + trading)

Served from the chart server (`/` or `/master`). Notable behaviors:

- **Panel visibility** — Header checkboxes toggle each major `.panel` (`chart-panel`, `activity-panel`, …). State is stored in `localStorage` under `master-panel-visibility`. The old **Focus chart** (`master-dashboard-minimal`) key is migrated once on load.
- **Chart** — Lightweight Charts with **no background grid**. Time-axis ticks: **time only** on intraday marks, **short date** on day-boundary marks. Crosshair **time scale label** and the OHLCV legend line show **date only** for the hovered bar. **Next bar** shows a countdown from last bar open time + selected timeframe (best-effort vs exchange alignment). **Fullscreen** uses the chart header button; **Escape** exits fullscreen (and still closes `.modal` overlays when not fullscreen).
- **Orders on chart** — After cancel / cancel-all / flatten / place, the server **invalidates** `state_cache` orders and clears the short **`/api/chart/orders` HTTP cache**; the client may call `GET /api/chart/orders?no_cache=1` after mutations. Client-side, terminal order statuses are filtered out of the orders table and order price lines; **`orderLineDataMap`** is cleared when lines refresh to avoid orphan “ghost” lines after drags.
- **Pop-out** — Removed (no `/popout` route, no pop-out buttons).

## CORS

- `aiohttp_cors` is required (pinned in [requirements.txt](../requirements.txt)); see [CHANGELOG.md](CHANGELOG.md).
