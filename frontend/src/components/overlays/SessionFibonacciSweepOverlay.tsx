import { useEffect, useRef } from 'react'
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts'
import {
  DOWN_COLOR,
  buildSessionFibGrids,
  type BuildOverlayOptions,
  type OverlayBar,
  type SessionFibGrid,
} from '../../utils/sessionFibonacciSweepMath'

export interface SessionFibonacciSweepOverlayProps {
  chart: IChartApi | null
  series: ISeriesApi<'Candlestick'> | null
  /** Chart container element (for sizing the canvas) */
  container: HTMLDivElement | null
  bars: OverlayBar[]
  enabled: boolean
  options?: BuildOverlayOptions
}

function hexAlpha(hex: string, alpha: number): string {
  const h = hex.replace('#', '')
  const full =
    h.length === 3
      ? h
          .split('')
          .map((c) => c + c)
          .join('')
      : h
  const r = parseInt(full.slice(0, 2), 16)
  const g = parseInt(full.slice(2, 4), 16)
  const b = parseInt(full.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${alpha})`
}

function drawGrids(
  ctx: CanvasRenderingContext2D,
  chart: IChartApi,
  series: ISeriesApi<'Candlestick'>,
  grids: SessionFibGrid[],
  width: number,
  height: number,
) {
  ctx.clearRect(0, 0, width, height)
  const ts = chart.timeScale()

  for (const grid of grids) {
    const x1 = ts.timeToCoordinate(grid.startTime as Time)
    const x2 = ts.timeToCoordinate(grid.endTime as Time)
    if (x1 === null || x2 === null) continue
    const left = Math.min(x1, x2)
    const right = Math.max(x1, x2)
    if (right < 0 || left > width) continue

    const yAnchor = series.priceToCoordinate(grid.anchor)
    if (yAnchor !== null) {
      ctx.strokeStyle = hexAlpha(grid.accent, 0.95)
      ctx.lineWidth = 2
      ctx.beginPath()
      ctx.moveTo(left, yAnchor)
      ctx.lineTo(right, yAnchor)
      ctx.stroke()
    }

    for (const z of grid.zones) {
      for (const band of [z.up, z.down]) {
        const yTop = series.priceToCoordinate(band.hi)
        const yBot = series.priceToCoordinate(band.lo)
        if (yTop === null || yBot === null) continue
        const top = Math.min(yTop, yBot)
        const bot = Math.max(yTop, yBot)
        const fill = band === z.up ? hexAlpha(grid.accent, 0.28) : hexAlpha(DOWN_COLOR, 0.28)
        const border = band === z.up ? hexAlpha(grid.accent, 0.75) : hexAlpha(DOWN_COLOR, 0.75)
        ctx.fillStyle = fill
        ctx.fillRect(left, top, Math.max(1, right - left), Math.max(1, bot - top))
        ctx.strokeStyle = border
        ctx.lineWidth = 1
        ctx.strokeRect(left, top, Math.max(1, right - left), Math.max(1, bot - top))
      }
    }
  }
}

/**
 * Canvas overlay synced to a lightweight-charts instance.
 * Draws session fib 0 + zone boxes for Tokyo / London / NY AM / NY PM.
 */
export default function SessionFibonacciSweepOverlay({
  chart,
  series,
  container,
  bars,
  enabled,
  options,
}: SessionFibonacciSweepOverlayProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const gridsRef = useRef<SessionFibGrid[]>([])

  useEffect(() => {
    if (!enabled || !bars.length) {
      gridsRef.current = []
      const c = canvasRef.current
      if (c) {
        const ctx = c.getContext('2d')
        if (ctx) ctx.clearRect(0, 0, c.width, c.height)
      }
      return
    }
    gridsRef.current = buildSessionFibGrids(bars, options)
  }, [bars, enabled, options])

  useEffect(() => {
    if (!chart || !series || !container || !canvasRef.current) return

    const canvas = canvasRef.current
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const redraw = () => {
      const rect = container.getBoundingClientRect()
      const dpr = window.devicePixelRatio || 1
      const w = Math.max(1, Math.floor(rect.width))
      const h = Math.max(1, Math.floor(rect.height))
      if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
        canvas.width = w * dpr
        canvas.height = h * dpr
        canvas.style.width = `${w}px`
        canvas.style.height = `${h}px`
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      }
      if (!enabled) {
        ctx.clearRect(0, 0, w, h)
        return
      }
      drawGrids(ctx, chart, series, gridsRef.current, w, h)
    }

    redraw()

    const ts = chart.timeScale()
    ts.subscribeVisibleLogicalRangeChange(redraw)
    chart.subscribeCrosshairMove(redraw)
    const ro = new ResizeObserver(() => redraw())
    ro.observe(container)
    const interval = window.setInterval(redraw, 400)

    return () => {
      ts.unsubscribeVisibleLogicalRangeChange(redraw)
      chart.unsubscribeCrosshairMove(redraw)
      ro.disconnect()
      window.clearInterval(interval)
    }
  }, [chart, series, container, enabled, bars, options])

  return (
    <canvas
      ref={canvasRef}
      className="pointer-events-none absolute inset-0 z-[5]"
      aria-hidden
    />
  )
}
