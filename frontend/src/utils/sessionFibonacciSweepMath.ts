/**
 * Session Fibonacci Sweep — pure math + bar scanner for chart overlays.
 * Keep ratios / zones / default clocks in sync with
 * strategies/session_fibonacci_sweep_math.py and the Pine indicator.
 */

export const FIB_RATIOS = [
  0.236, 0.2795, 0.5, 0.559, 0.8365, 1.0, 1.382, 1.5, 1.618,
] as const

export type ZoneName = 'inner' | 'mid' | 'full' | 'extension'
export type Side = 'up' | 'down'

export const ZONES: Record<ZoneName, [number, number]> = {
  inner: [0.236, 0.2795],
  mid: [0.5, 0.559],
  full: [0.8365, 1.0],
  extension: [1.382, 1.618],
}

export const ZONE_ORDER: ZoneName[] = ['inner', 'mid', 'full', 'extension']

export const DEFAULT_TIMEZONE = 'America/New_York'

export interface SessionClock {
  name: string
  /** "HH:MM" America/New_York */
  start: string
  /** "HH:MM" America/New_York */
  end: string
  color: string
}

/** Matches Pine defaults */
export const DEFAULT_SESSIONS: SessionClock[] = [
  { name: 'Tokyo', start: '18:30', end: '00:00', color: '#2962FF' },
  { name: 'London', start: '01:30', end: '05:00', color: '#FF9800' },
  { name: 'NY AM', start: '08:00', end: '11:00', color: '#089981' },
  { name: 'NY PM', start: '13:00', end: '16:00', color: '#7E57C2' },
]

export const DOWN_COLOR = '#f23645'

export interface ZoneBand {
  name: ZoneName
  lo: number
  hi: number
  mid: number
}

export interface SessionFibGrid {
  sessionName: string
  accent: string
  /** unix seconds */
  startTime: number
  /** unix seconds — right edge of drawings */
  endTime: number
  anchor: number
  distance: number
  zones: Array<{ name: ZoneName; up: ZoneBand; down: ZoneBand }>
}

export interface OverlayBar {
  time: number
  open: number
  high: number
  low: number
  close: number
}

export interface BuildOverlayOptions {
  sessions?: SessionClock[]
  /** ATR of last N like-session ranges (default) or previous only */
  rangeMode?: 'atr' | 'previous'
  atrLen?: number
  delayUntilIB?: boolean
  ibMinutes?: number
  /** Which zones to draw — default matches Pine: inner/mid/ext */
  enabledZones?: ZoneName[]
  historyKeep?: number
  gridWidthFrac?: number
  timezone?: string
}

function parseHm(hm: string): number {
  const [h, m] = hm.split(':').map((x) => Number(x))
  return (h || 0) * 60 + (m || 0)
}

/** Minutes-of-day in a named IANA timezone. */
export function minutesOfDayTz(unixSec: number, timeZone: string): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(unixSec * 1000))
  let hour = Number(parts.find((p) => p.type === 'hour')?.value ?? 0)
  const minute = Number(parts.find((p) => p.type === 'minute')?.value ?? 0)
  if (hour === 24) hour = 0
  return hour * 60 + minute
}

export function inSessionWindow(mod: number, startHm: string, endHm: string): boolean {
  const start = parseHm(startHm)
  const end = parseHm(endHm)
  if (start === end) return true
  if (start < end) return mod >= start && mod < end
  return mod >= start || mod < end
}

export function projectLevels(anchor: number, distance: number, ratios: readonly number[] = FIB_RATIOS) {
  if (!(distance > 0)) throw new Error('distance must be positive')
  const up: Record<number, number> = {}
  const down: Record<number, number> = {}
  for (const r of ratios) {
    up[r] = anchor + r * distance
    down[r] = anchor - r * distance
  }
  return { anchor, distance, up, down }
}

export function zoneBand(anchor: number, distance: number, name: ZoneName, side: Side): ZoneBand {
  const [loR, hiR] = ZONES[name]
  if (side === 'up') {
    const lo = anchor + loR * distance
    const hi = anchor + hiR * distance
    return { name, lo, hi, mid: (lo + hi) / 2 }
  }
  const hi = anchor - loR * distance
  const lo = anchor - hiR * distance
  return { name, lo, hi, mid: (lo + hi) / 2 }
}

export function avgLikeSessionRanges(ranges: number[], n: number): number | null {
  if (n < 1 || ranges.length === 0) return null
  const take = ranges.slice(-n)
  if (!take.length) return null
  return take.reduce((a, b) => a + b, 0) / take.length
}

export function resolveDistance(
  lastRange: number | null,
  ranges: number[],
  mode: 'atr' | 'previous' = 'atr',
  atrLen = 5,
  minTick = 0,
): number | null {
  const d = mode === 'atr' ? avgLikeSessionRanges(ranges, atrLen) : lastRange
  if (d == null || d <= minTick) return null
  return d
}

interface RunState {
  inSession: boolean
  startIdx: number
  startTime: number
  sessOpen: number
  runHigh: number
  runLow: number
  gridStarted: boolean
  ranges: number[]
  lastRange: number | null
  printed: SessionFibGrid[]
}

function makeRun(): RunState {
  return {
    inSession: false,
    startIdx: -1,
    startTime: 0,
    sessOpen: NaN,
    runHigh: NaN,
    runLow: NaN,
    gridStarted: false,
    ranges: [],
    lastRange: null,
    printed: [],
  }
}

/**
 * Scan OHLC bars and emit session fib grids (zones around fib 0).
 * Defaults mirror the Pine indicator (ATR n=5, IB 30m, inner/mid/ext).
 */
export function buildSessionFibGrids(
  bars: OverlayBar[],
  options: BuildOverlayOptions = {},
): SessionFibGrid[] {
  if (!bars.length) return []

  const sessions = options.sessions ?? DEFAULT_SESSIONS
  const rangeMode = options.rangeMode ?? 'atr'
  const atrLen = options.atrLen ?? 5
  const delayUntilIB = options.delayUntilIB ?? true
  const ibMinutes = options.ibMinutes ?? 30
  const enabledZones = options.enabledZones ?? (['inner', 'mid', 'extension'] as ZoneName[])
  const historyKeep = options.historyKeep ?? 2
  const gridWidthFrac = options.gridWidthFrac ?? 0.85
  const timezone = options.timezone ?? DEFAULT_TIMEZONE

  const runs = new Map<string, RunState>()
  for (const s of sessions) runs.set(s.name, makeRun())

  const allGrids: SessionFibGrid[] = []

  const tryPrint = (clock: SessionClock, run: RunState, barIdx: number) => {
    if (run.gridStarted || !run.inSession) return
    const bar = bars[barIdx]
    if (delayUntilIB) {
      const ibEnd = run.startTime + ibMinutes * 60
      if (bar.time < ibEnd) return
      // Anchor = previous bar close if still inside IB window, else this open
      const prev = barIdx > 0 ? bars[barIdx - 1] : null
      if (prev && prev.time < ibEnd) {
        run.sessOpen = prev.close
      } else if (!Number.isFinite(run.sessOpen)) {
        run.sessOpen = bar.open
      }
    }

    const dist = resolveDistance(run.lastRange, run.ranges, rangeMode, atrLen)
    if (dist == null) return

    // Right edge: fraction of remaining session span from print bar
    let sessEndIdx = barIdx
    for (let j = barIdx; j < bars.length; j++) {
      const mod = minutesOfDayTz(bars[j].time, timezone)
      if (!inSessionWindow(mod, clock.start, clock.end)) break
      sessEndIdx = j
    }
    const leftTime = delayUntilIB ? bar.time : run.startTime
    const span = Math.max(1, bars[sessEndIdx].time - leftTime)
    const rightTime = leftTime + Math.max(1, Math.floor(span * gridWidthFrac))

    const zones = enabledZones.map((name) => ({
      name,
      up: zoneBand(run.sessOpen, dist, name, 'up'),
      down: zoneBand(run.sessOpen, dist, name, 'down'),
    }))

    const grid: SessionFibGrid = {
      sessionName: clock.name,
      accent: clock.color,
      startTime: leftTime,
      endTime: rightTime,
      anchor: run.sessOpen,
      distance: dist,
      zones,
    }
    run.printed.push(grid)
    allGrids.push(grid)
    run.gridStarted = true
  }

  const endSession = (run: RunState) => {
    if (!run.inSession) return
    const rng = run.runHigh - run.runLow
    if (Number.isFinite(rng) && rng > 0) {
      run.lastRange = rng
      run.ranges.push(rng)
      while (run.ranges.length > Math.max(atrLen, 20)) run.ranges.shift()
    }
    run.inSession = false
    run.gridStarted = false
  }

  for (let i = 0; i < bars.length; i++) {
    const bar = bars[i]
    const mod = minutesOfDayTz(bar.time, timezone)

    for (const clock of sessions) {
      const run = runs.get(clock.name)!
      const inside = inSessionWindow(mod, clock.start, clock.end)

      if (inside && !run.inSession) {
        run.inSession = true
        run.startIdx = i
        run.startTime = bar.time
        run.sessOpen = bar.open
        run.runHigh = bar.high
        run.runLow = bar.low
        run.gridStarted = false
        if (!delayUntilIB) tryPrint(clock, run, i)
      } else if (inside && run.inSession) {
        run.runHigh = Math.max(run.runHigh, bar.high)
        run.runLow = Math.min(run.runLow, bar.low)
        tryPrint(clock, run, i)
      } else if (!inside && run.inSession) {
        endSession(run)
      }
    }
  }

  // Keep last historyKeep completed per session + any still-active print
  const kept: SessionFibGrid[] = []
  for (const clock of sessions) {
    const run = runs.get(clock.name)!
    const list = run.printed
    const slice = list.slice(-Math.max(1, historyKeep + 1))
    kept.push(...slice)
  }
  // Prefer chronological order
  kept.sort((a, b) => a.startTime - b.startTime)
  return kept.length ? kept : allGrids.slice(-Math.max(4, historyKeep * sessions.length))
}
