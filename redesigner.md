I have a comprehensive picture. Let me now write the design-oriented breakdown for another web designer.

# `master` GUI — Design Breakdown for Another Web Designer

## 1. What it is in one paragraph

`python trading_bot.py --command='master'` runs `_handle_master_gui` in `core/cli_command_parser.py:509-564`, which boots an in-process `aiohttp` server (`gui/chart_html.py`) on a localhost port, then opens `http://127.0.0.1:<port>/master` in the default browser. The page itself is a single static HTML document, `gui/master_control.html` (~6,750 lines), with all CSS and JS inlined. There is no build step, no SPA framework, no bundler — it's a **single-file, zero-build, hand-written dashboard** that talks to the local Python process via REST (`/api/chart/*`) and a single WebSocket (`/ws`) for push updates. The companion file is `gui/chart_html.py` (the aiohttp routes + WS broadcaster).

A canonical "static dashboard" exists separately under `static/dashboard/` for Railway deployment; the `master` GUI is the **dev / local trader cockpit** built around the chart.

## 2. Tech stack & loading

- **Framework**: none. Plain HTML5 + vanilla JS, IIFE pattern (`(function(){ 'use strict'; … })()`).
- **Charting**: TradingView **Lightweight Charts 4.1.3** loaded from unpkg CDN (`<script src="https://unpkg.com/lightweight-charts@4.1.3/…">`). Candlestick + volume histogram overlay.
- **Fonts**: system stack — `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`. Monospace for terminals/logs is `'Courier New', ui-monospace, monospace`.
- **Icons / glyphs**: Unicode only (`⠿` drag handle, `⛶` fullscreen, `−` collapse, `×` close). No icon library.
- **Configuration injection**: server-side string replacement of `{{SERVER_PORT}}`, `{{SYMBOL}}`, `{{TIMEFRAME}}` placeholders in a `<meta id="tb-config">` element; JS reads them off `dataset`.
- **Persistence**: `localStorage` for user prefs (panel visibility, panel order, alerts on/off, shortcuts on/off, desktop-notifs on/off); `sessionStorage` for the per-tab "Session theme" override.

## 3. Visual design language

### 3.1 Palette (default theme — "Warm beige / brown + dusty blue")

All colors are CSS custom properties on `:root` so the whole UI re-skins instantly:

```281:286:gui/master_control.html
            --bg-primary: #1c1814;
            --bg-secondary: #2a241d;
            --bg-tertiary: #3d352c;
            --text-primary: #faf6f0;
            --text-secondary: #c9b8a4;
            --border-color: #6f5e4f;
```

Plus `--accent-color: #6a9fb8` (dusty blue), `--positive-color: #7aab7f` (sage green), `--negative-color: #c4877a` (terracotta), `--warning-color: #d4a84b`, `--chart-canvas-bg: #000000`.

Design intent: low-contrast warm dark UI for long sessions, with **muted earth tones for chrome and saturated semantic colors** (green/red/amber/blue) reserved exclusively for state. Pure black is preserved only for the chart canvas, command input, terminal output, and the logs pane (CRT-style green-on-black text in logs).

### 3.2 Theme switcher

There's a built-in **session theme popover** (modal centered, ~420×640 max) reachable from a top-bar button. Two layers:

1. **Server theme** loaded once via `GET /api/chart/theme/page` (CSS vars from `config/page_theme.json` template).
2. **Session theme** — color pickers + hex input per CSS var, plus 7 preset cards (`Earth grid`, `Khaki · almond`, `Apricot · rose`, `Modern neutral`, `Neutral boho`, `Neutral summer`, `Lavender house`). Each preset ships both a CSS-var dictionary and a matching chart-color dict (candle up/down, crosshair, scale border, volume bar). Applies immediately, persists in `sessionStorage` only — closing the tab resets to server theme.

### 3.3 Type scale

Tiny, dense, by design. Body is `12px`. Status labels `8–9px` uppercase, status values `10–11px`, panel titles `11px`, table rows `10–11px`. Typography is the primary information-density lever; padding is the secondary one (most cells use `padding: 4–6px`).

### 3.4 Component primitives

- **`.panel`**: `var(--bg-secondary)` card, 1px border, 4px radius, soft 2-layer drop shadow, `display:flex; flex-direction:column`. Each panel has a header (`h2`) and `.panel-content` body. Resizable corner/edge handles (4–8px) appear on hover; opacity-0 widget-control buttons fade in on `panel:hover`.
- **`.panel h2`**: clickable to collapse (animated `max-height` + `padding` + `opacity`), with a `−` rotate-on-collapse caret, a `⠿` drag handle, and per-panel widget controls (e.g. fullscreen for chart).
- **Buttons**: 5 semantic flavors — `.btn-primary` (accent), `.btn-buy` (positive), `.btn-sell` / `.btn-danger` (negative), `.btn-info` (blue). Hover = `filter: brightness(1.08–1.12)`, no transform. Compact buttons override font-size to `9px` and height to `22px`.
- **Inputs / selects**: `var(--bg-primary)` background, 1px border, 2-3px radius, `10px` font. Numeric inputs for prices use `step="0.25"` (futures tick).

### 3.5 Status semantics

- Positive P/L → `--positive-color` (sage)
- Negative P/L → `--negative-color` (terracotta)
- Errors / risk alerts → red flash (full-screen overlay), high-frequency 400 Hz sawtooth tone
- Take-profit fill → bright green flash, double-beep at 1200 Hz
- Stop triggered → 300 Hz sawtooth + red flash

The audio system is a hand-rolled `AudioContext` with `OscillatorNode` (no audio files) — `playAlert(type)` maps types like `success/error/warning/info/fill/order/stop_triggered/take_profit` to (frequency, waveform, single/double-beep envelope). All sound is **off by default** and toggleable from the top bar.

## 4. Layout & responsive behavior

### 4.1 Sticky top command bar

```1036:1086:gui/master_control.html
    <div class="top-command-bar">
        <div class="account-strip">
```

Single row (`flex-wrap: wrap`, `min-height: 34px`, sticky at `top:0`, `z-index:100`):

- **Left "account strip"**: Account `<select>` (180px), then 5 KPI tiles — Balance, Unrealized P&L, Realized P&L, Open Positions, Open Orders. Each tile uses an 8px-uppercase label over a 10px value.
- **Right tail (`flex: 1 1 auto`, right-aligned)**: a "Panels:" checkbox row to show/hide each panel, then five toolbar buttons (`Shortcuts ON`, `Alerts OFF`, `Notifications OFF`, `Help`, `Theme`), then a connection-status pill, browser-tab uptime counter, and a WebSocket lag indicator ("WS 1.2s"). The active state of the toggle buttons is conveyed by background color (accent vs. tertiary).

### 4.2 Main dashboard grid

```129:141:gui/master_control.html
        .dashboard {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            padding: 10px;
            width: 100%;
            max-width: 1800px;
            min-height: 100vh;
            margin: 0 auto;
            align-content: flex-start;
            background: var(--bg-primary);
        }
```

It is **NOT** CSS Grid for the main layout — it's a wrapping flexbox with hard-coded panel widths. Each panel sets its own `width`, `min-height`, and `flex: 0 0 auto` (the chart panel is `flex: 1 1 800px` to absorb extra horizontal space). Order is controlled via `order: 1..6` and persisted in `localStorage` after the user drags. The viewport breakpoint at `1400px` flips it to a single column.

The default layout (before user drag) is roughly:
```
┌──────────────────────────────────────┬──────────────────┐
│ Chart panel (flex 1, 600x500 min)    │ Active Trading   │
│ order:1                              │ 400×500   order:2│
│                                      ├──────────────────┤
│                                      │ Strategy Hub     │
│                                      │ 400×500   order:3│
├────────────────────┬─────────────────┴──────────────────┤
│ Trades panel       │ Terminal Hub                       │
│ 750×500   order:3? │ 750×500   order:4                  │
└────────────────────┴────────────────────────────────────┘
… Performance 400×500 order:5  ·  Strategy Hub 400×500 order:6
```

Heights are caps, not fixed: each panel has `resize: both` (where applicable) so the user can drag-resize.

### 4.3 Drag-to-reorder & per-panel collapse

- **Reorder**: HTML5 drag/drop on each panel's `⠿` handle. While dragging, source gets `.dragging { opacity: 0.5 }`; hovered target gets `.drag-over { border-color: accent; box-shadow: 0 0 14px rgba(255,255,255,0.06) }`. On drop, JS recomputes `order` and persists the array in `localStorage`.
- **Collapse**: clicking the `h2` toggles `.panel.collapsed`. Animation is a 300ms ease on `max-height`/`padding`/`opacity`. The `−` caret rotates `-90deg` when collapsed.
- **Visibility**: per-panel checkboxes in the top bar add a `panel-user-hidden` class (`display:none !important`).

### 4.4 Fullscreen chart

`body.chart-fullscreen-active` hides the top bar and removes dashboard padding; `#chart-panel.chart-fullscreen` becomes `position:fixed; inset:0; z-index:10050`. Toggled by a `⛶` button in the chart header or the chart context menu. Esc returns to normal.

## 5. Panels in detail

### 5.1 Chart panel (`#chart-panel`)

The hero element, three vertical zones inside the panel-content:

**Row 1 — Chart controls (compact strip):**
- Symbol `<select>` (populated dynamically), Timeframe `<select>` with 12 options from `30s` → `1d`.
- "Bars" history-source selector with three options: `Auto (API→file)`, `API only`, `Databento file`. The Databento option pulls up to **5000 bars** from local stitched CSVs for MNQ/MES/MGC. Initial visible window is capped at the most recent 200 bars; older bars exist in the series and are reachable by panning left.
- Buttons: `Refresh`, `Refresh All`.
- Inline **chart-context-strip** with five live KPIs that mirror the top bar (`Bal · U.P/L · Pos · Ords · Trades`).
- Right-aligned **refresh-rate selector** with 6 quanta: `1, 3, 6, 12 (default), 18, 24` updates per second.

**Row 2 — Trading controls (compact strip):**
- Order-type `<select>`: Market, Limit, Stop Market, Stop Limit, Trailing Stop. The dependent inputs (limit price, stop price, trail amount, bracket SL/TP prices) are toggled `display:none/inline-block` based on the chosen type.
- Qty `<input type="number">` (60px), Bracket checkbox.
- Big colored action buttons in fixed order: **BUY** (green), **SELL** (red), **CANCEL** (faded red, cancels all working orders), **FLATTEN** (bold red, closes all positions).
- Right-aligned cluster of five small "popout" buttons — `Activity / Performance / Strategies / Terminal / Tradeslog` — each opens a centered modal data popover (see §6.2).

**Row 3 — Chart canvas:**

```573:586:gui/master_control.html
        #chart-container {
            flex: 1 1 auto;
            min-height: 0;
            position: relative;   /* positioning context for #chart-legend */
            width: 100%;
            margin-top: 6px;
            padding: 6px 8px 22px 8px;   /* visual inset away from panel border + resize handle */
            box-sizing: border-box;
            background: var(--chart-canvas-bg, #000000);
            border-radius: 4px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }
```

`#chart-mount` is sized in JS (style.width/height) on each container resize via `ResizeObserver` + `requestAnimationFrame`, then `chart.resize(w, h, true)`. Inside the canvas, the design draws several overlays:

- **Top-left legend** (`.chart-legend`): translucent pill with Symbol + bar date + `Now:` clock + O/H/L/C/V values that update live and on crosshair move. Up bars colorize OHLC values green; down bars red.
- **Right-side last-price/countdown chip** (`#chart-lastprice-countdown`): tabular-nums monospace badge, vertically aligned to the price level, showing last close + countdown seconds until current bar closes.
- **Price lines** drawn directly on the candlestick series, three categories with distinct color/style:
  - **Position entry**: solid white, 2px, `axisLabel = "BUY 2 @ $19234.50"`.
  - **Working limit orders**: solid blue (`#2196f3`), 1–2px.
  - **Working stop orders**: dashed red (`#f44336`).
  - **Other working orders**: dotted orange (`#ff9800`).
  - **Bracket SL/TP**: 2px and **draggable** — drag the line to fire `POST /api/chart/order/modify`.
  - **Overnight-range OR High / OR Low**: solid purple (`#c084fc`), 2px (only when `overnight_range` is the active strategy).
- **Right-click context menu** with: `Copy crosshair close`, `Reload chart data`, `Fit time scale`, `Scroll to Active Trading`, `Bracket orders: drag price lines`.

### 5.2 Active Trading panel (`#activity-panel`)

Two stacked sub-sections sharing the same panel:

- **Positions** table — Symbol / Side / Qty / Entry / P&L / Risk / Actions (Close button per row).
- **Orders** table — Symbol / Side / Qty / Price / Type / Actions (Cancel button per row).

Sub-section headers use a 10px uppercase muted label with a divider rule. Empty state is a centered muted "No positions" / "No orders" cell. Hover row highlight = `var(--bg-tertiary)`.

### 5.3 Strategy Management panel (`#strategy-hub-panel`)

Two sub-sections:

- **Strategy Signals** (top) — filter input + type filter (`All Types / BUY / SELL / EXIT`) + Clear button; below is a scrollable signal-feed container (max-height 200px, tertiary background). Each signal becomes a row with strategy name, symbol, type, time.
- **Strategy Control** (below, divider) — a 4-column micro-form (`Strategy / Symbol(s) / Timeframe / Start button`) using `display: grid; grid-template-columns: 1fr 1fr 1fr auto`. Start button is positive-green. Below the form is the **active-strategy grid** (`auto-fill, minmax(250px, 1fr)`), each card showing strategy name, status pill (`active` green / `idle` gray), symbols/timeframe meta, and a small action row (Pause/Stop/Details).

### 5.4 Terminal panel (`#terminal-hub-panel`)

Two sub-sections:

- **Command Input** — flex row of black-background `<input>` with monospace `'Courier New'` 10px, plus a green "Execute" button. Below: a black `#command-output` box (max-height 150px, font-size 9px) that streams response lines.
- **Terminal Logs** — filter row (Level select: All / INFO / WARNING / ERROR; text search; Clear button; right-aligned "Auto-scroll" checkbox). The log container is **the most distinctive UI element**: black background, default green text (`#00ff00`), 9px Courier, vertical-resize cursor, fixed `min-height: 250px`, `max-height: 600px`. Each line shows a muted timestamp, a colored level pill (green/orange/red), and the message colored by level. Up to 500 lines kept in memory (sliding window).

### 5.5 Performance Metrics panel (`#performance-panel`)

Three vertical sections inside one panel:

- **Top metrics widget** (`#performance-metrics-widget`) — a grid of metric cards, each with a small label and a big number. `.metric-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px; border-color: accent; }`.
- **Equity Curve (24h)** — a small inline P&L sparkline rendered as SVG (no external lib), `min-height: 60px`, on a tertiary-bg rounded card.
- **Risk & Compliance** — bottom section with daily-loss-limit / max-loss-limit progress indicators (the WS handler colors and flashes the UI red when DLL or MLL ≥ 80–90%).

### 5.6 Trading Results panel (`#trades-panel`)

The largest data table in the app:

- **Summary bar**: 6 stat tiles in a `repeat(auto-fit, minmax(100px, 1fr))` grid with a subtle linear gradient — Trades / Win Rate / Net P&L / Avg Trade / Max DD / Profit Factor.
- **Controls row**: Side filter (`All / LONG / SHORT`), `Export CSV` button (accent), `Refresh` button (positive), trade-count text.
- **Trades table**: 11 columns (`# / Side / Entry Time / Exit Time / Entry / Exit / Points / P&L / Fees / Cumulative P&L`). Sticky `<thead>` with sortable columns (each header has a `<span id="sort-indicator-…">` that displays an arrow when active). 8×4 padding per cell, hover highlight, scrollable up to 500px tall.

## 6. Modals & overlays

### 6.1 Theme session popover

Centered fixed modal (`translate(-50%, -50%)`, max `420×640px`), z-index 16001 over a `rgba(0,0,0,0.45)` backdrop (16000). Header bar with title and `×`. Body has:
- Preset row of 7 cards, each a 88–120px column with a 4-color swatch strip on top and the preset name below.
- Per-variable rows: 120px label + 40×28 native `<input type="color">` swatch + monospace hex `<input type="text">`.
- "Reset & reload" footer button.

### 6.2 Compact data popouts

Top-right anchored modal (`top: 96px; right: 14px`, 420×520 max, z-index 15001) for the five chart-panel popout buttons. Skeletal loading state shows "Loading…" muted gray, then renders a typed table:
- `activity` → Positions table + Working orders table
- `performance` → 2-column grid of 6 KPI cards (Trades / Win rate / Net P&L / Return / Max DD / Avg trade)
- `tradeslog` → simple recent-trades table
- `strategies` → strategy status table
- `terminal` → mirrored snapshot of the last ~12,000 chars of the logs container plus an "Open Terminal panel" deep-link

### 6.3 Toasts

Bottom-right stack (`position: fixed; right:12px; bottom:12px; z-index:20000; pointer-events:none`). 4 types — `success / error / warning / info` — color-coded backgrounds. Auto-dismiss with `timeoutMs` (default 3500ms; 6000ms for the help text; 10000ms for risk alerts).

### 6.4 Full-screen flash

A single transient `<div>` covering the viewport with a colored translucent overlay, animated to fade out over ~300ms. Used for fills, take-profit, stops, and risk alerts. This is the "subliminal" channel that doesn't require reading the toast.

## 7. Data flow

### 7.1 REST endpoints (consumed by JS)

All under `http://127.0.0.1:<port>` (chart_html.py routes, see `gui/chart_html.py:2551-3798`):

| Group | Endpoints |
|------|------|
| Chart data | `GET /api/chart/quote`, `/api/chart/reload?source=auto|api|databento`, `/api/chart/contracts` |
| Trading | `POST /api/chart/order`, `/api/chart/cancel_order`, `/api/chart/cancel_all`, `/api/chart/flatten`, `/api/chart/close_position` |
| State | `GET /api/chart/positions`, `/api/chart/orders?include_linked=0`, `/api/chart/account/state` |
| Strategy | `GET /api/chart/strategy/status`, `/api/chart/strategy/details/{name}`; `POST /api/chart/strategy/start`, `/strategy/stop` |
| Performance | `GET /api/chart/pnl/history`, `/api/chart/trades`, `/api/chart/performance/metrics`, `/api/chart/risk/metrics` |
| Theme | `GET /api/chart/theme/page` |
| Account | `GET /api/accounts`, `POST /api/select_account` |
| Command | `POST /api/execute_command` |

### 7.2 WebSocket (`/ws`)

- **Single duplex channel** with a 30s ping keepalive and exponential-backoff reconnect (max 30s, after which the client falls back to **HTTP polling**).
- Server batches messages every **83ms (12×/s)** and merges by `type` (latest of each type wins) before flushing.
- Message types the UI handles: `account / account_update / position_update / positions / order_update / orders / strategies / log / signal / pnl_history / session_trades / metrics_update / performance_metrics / risk_update / risk_metrics / order_filled / position_fill / stop_triggered / take_profit_hit / risk_alert / pong / heartbeat / order_updated / order_canceled / order_cancelled / order_rejected / position_closed`.
- Connection state is shown as a small colored pill in the top-right ("Connecting…" → "Connected (WebSocket)" green → "Connected (Polling)" amber → "Disconnected" / "Connection Error" red).

## 8. Keyboard shortcuts

Global (when not focused inside an `<input>` / `<textarea>`):

| Key | Action |
|----|----|
| `B` | Place market BUY at current qty |
| `S` | Place market SELL at current qty |
| `F` | Flatten all positions |
| `C` | Cancel all working orders |
| `R` | Refresh all panels |
| `?` | Show shortcut help toast |
| `Esc` | Exit chart fullscreen / close any modals |

Toggleable via the `Shortcuts ON/OFF` button in the top bar; persisted to `localStorage`.

## 9. Notable design decisions for a redesigner to know

1. **Information density over breathing room.** Every effort goes into showing more on screen. Don't add 16–24px padding around tables/inputs; the design tolerates `4–8px` paddings and `8–11px` font sizes intentionally.
2. **No third-party UI library.** No Tailwind, Bootstrap, MUI, etc. All styles are in one `<style>` block; all class names are flat (`.panel`, `.btn-buy`, `.chart-controls.compact`).
3. **CSS variables are the single source of truth** for color. If you change a hex value anywhere outside `:root`, you'll likely break the theme system.
4. **Chart is special.** Lightweight Charts injects a `.tv-lightweight-charts` table that the global table CSS would otherwise ruin — there is an explicit override block (`#chart-mount .tv-lightweight-charts td { padding:0 !important; … }`) that must remain whenever you touch table styling.
5. **Resize + reflow is JS-driven.** The chart mount is sized via JS each frame (not pure flexbox) because LWC needs a definite size at `createChart` time. Don't try to replace this with `width:100%; height:100%` alone.
6. **Two layout systems coexist** in the file. The main dashboard is wrapping flex. There is also a media-query CSS-Grid fallback (lines ~873–883) that activates below 1400px — if you redesign the main layout, update both.
7. **Nothing is per-route.** It's a single page; the "popouts" are inline modals, not separate documents.
8. **Color = state, never decoration.** Green/red/amber/blue all carry meaning (positive / negative / warning / accent action). Don't reach for them just for visual variety.
9. **Audio + flash + toast + desktop notification are independent channels.** A single event (e.g. fill) can fire all four; users disable any of them independently.
10. **The HTML file is templated server-side** by simple string replace, including a regex pass that re-applies `selected` to the right `<option>` in the timeframe select. If you add new placeholders, mirror them in `chart_html.py:handle_master_control` (lines 3833–3847).

## 10. Files a redesigner needs to touch

- `gui/master_control.html` — all visual design, CSS, JS lives here.
- `gui/chart_html.py` — REST routes, WebSocket broadcaster, server-side templating, theme JSON loader.
- `config/page_theme.json` (optional) — server-default theme override.
- `gui/README.md` and `docs/DASHBOARD.md` — keep documentation in sync.

If you plan a ground-up redesign, the cleanest cut is to keep `chart_html.py` (the API contract) and replace `master_control.html` wholesale. The endpoint surface in §7 is your stable contract.