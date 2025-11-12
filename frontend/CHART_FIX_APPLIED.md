# 🔧 Chart Migration Notes – Lightweight Charts v5

## What Changed

- Upgraded `lightweight-charts` from **v4.2.0 → v5.0.9**
- Replaced deprecated `chart.addCandlestickSeries / addHistogramSeries` with `chart.addSeries(...)`
- Integrated the new `createSeriesMarkers` plugin for position markers
- Added cleanup for price lines to prevent duplicate order overlays
- Verified build & type-check with the new API surface

## Key Commands

```bash
# Upgrade dependency
npm install lightweight-charts@5.0.9

# Validate TypeScript types
npm run type-check

# Produce production bundle
npm run build
```

## Updated Code Patterns

```typescript
import {
  CandlestickSeries,
  HistogramSeries,
  createSeriesMarkers,
} from 'lightweight-charts'

const chart = createChart(container, options)

const candleSeries = chart.addSeries(CandlestickSeries, candleOptions)
const volumeSeries = chart.addSeries(HistogramSeries, volumeOptions)

const markersPlugin = createSeriesMarkers(candleSeries, [])
markersPlugin.setMarkers(markersFromPositions)
```

```typescript
// Price line hygiene
priceLines.current.forEach(line => candleSeries.removePriceLine(line))
priceLines.current = []
```

## Bundle Snapshot (post-upgrade)

```
> npm run build

- chart-vendor: 383.04 kB │ gzip: 104.96 kB
- main bundle:  354.85 kB │ gzip: 102.35 kB
- react-vendor: 161.81 kB │ gzip:  52.78 kB
- styles:        24.48 kB │ gzip:   5.15 kB
```

## Testing Checklist

- [x] `npm run type-check`
- [x] `npm run build`
- [x] Chart renders without console errors
- [x] Position markers display & hide with toggle
- [x] Pending order lines clear when orders change
- [x] WebSocket updates move the active candle

## Next Steps

- Monitor in staging/production with live data
- Gather feedback on marker & order overlays
- Plan follow-up work (indicators, annotations, multi-pane layouts)

---

**Applied**: November 12, 2025  
**Version**: lightweight-charts@5.0.9  
**Status**: ✅ Migration complete

