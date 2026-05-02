# TradingView Lightweight Charts — customization map

This repo embeds **v4** of [Lightweight Charts](https://tradingview.github.io/lightweight-charts/) in `gui/master_control.html` (master dashboard) and may generate standalone HTML from `gui/chart_html.py`. Use this page as a checklist when tuning visuals.

## Where options live

| Area | Typical API | File |
|------|-------------|------|
| Chart root | `createChart(container, options)` | `master_control.html` → `initChart` |
| Series | `addCandlestickSeries`, `addHistogramSeries`, `addLineSeries` | same |
| Runtime | `chart.applyOptions`, `series.applyOptions`, `chart.timeScale().applyOptions` | same / quote refresh |
| Theme template (JSON) | Reference copy | `config/chart_theme.template.json` |
| Regenerate template file | — | `python scripts/init_chart_theme_template.py` |

## High-value `createChart` option groups

1. **`layout`** — `background`, `textColor`, `fontSize` (dark theme baseline).
2. **`grid`** — `vertLines` / `horzLines` `visible`, `color`, `style` (`Dotted`, `Solid`, …).
3. **`crosshair`** — `mode` (`Normal`, `Magnet`, `Hidden`), `vertLine` / `horzLine` colors, `labelVisible`, `width`.
4. **`rightPriceScale`** / **`leftPriceScale`** — `autoScale`, `scaleMargins`, `borderVisible`, `entireTextOnly`.
5. **`timeScale`** — `timeVisible`, `secondsVisible`, `borderColor`, `tickMarkFormatter`, `fixLeftEdge`, `lockVisibleTimeRangeOnResize`, `shiftVisibleRangeOnNewBar`, `barSpacing`, `minBarSpacing`.
6. **`localization`** — `locale`, `timeFormatter`, `priceFormatter` (custom axis text).
7. **`watermark`** — logo / subtle text overlay.
8. **`handleScroll` / `handleScale`** — mouse / touch interaction toggles.

## Series-level knobs (per series)

- **Candles**: `upColor`, `downColor`, `wickUpColor`, `wickDownColor`, `borderVisible`, `priceFormat`, `priceLineVisible`.
- **Histogram (volume)**: `color`, `base`, `priceScaleId` (`''` = overlay), `scaleMargins`.
- **Lines** (OR levels, orders): `color`, `lineWidth`, `lineStyle`, `lastValueVisible`, `priceLineSource`.

## Runtime behaviors (not constructor options)

- **Crosshair legend** — `chart.subscribeCrosshairMove` (OHLCV + “Now” clock in master).
- **Visible range** — `timeScale().setVisibleRange`, `fitContent`, `scrollToRealTime`.
- **Price lines** — `series.createPriceLine({ price, title, color, lineStyle, … })`.
- **Markers** — `series.setMarkers` for trades / signals.

## Optional upgrades (not wired by default)

- **Plugins** — v5 plugin model; repo targets v4 API in HTML.
- **Multiple panes** — separate chart instances or overlay series with `priceScaleId`.
- **Custom series** — only if you fork to v5+ APIs.

## Workflow: change theme safely

1. Edit `config/chart_theme.template.json` (or run `scripts/init_chart_theme_template.py` to reset).
2. Mirror the JSON block into `initChart({ ... })` in `gui/master_control.html` (JS uses `LightweightCharts.CrosshairMode.Normal`, not the string `"Normal"` in JSON).
3. Reload `/master`; confirm `tickMarkFormatter` / `localization` still match intraday vs day ticks.

## Related

- **Master page shell** (header, panels, typography): [`PAGE_THEME_OPTIONS.md`](PAGE_THEME_OPTIONS.md) — `config/page_theme.template.json` + `/api/chart/theme/page`.
- `docs/BACKTESTING.md` — chart is not the backtest UI; OR lines use `/api/chart/strategy/details/overnight_range`.
- `README.md` — master dashboard + executor OR snapshot behavior.
