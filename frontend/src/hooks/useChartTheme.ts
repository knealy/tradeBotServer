import { useMemo } from 'react'
import { DeepPartial, ChartOptions, ColorType, LineStyle } from 'lightweight-charts'

export type ChartTheme = 'dark' | 'light'

interface UseChartThemeOptions {
  theme?: ChartTheme
  height?: number
}

export function useChartTheme({ theme = 'dark', height = 500 }: UseChartThemeOptions = {}): DeepPartial<ChartOptions> {
  return useMemo(() => {
    const isDark = theme === 'dark'

    return {
      layout: {
        background: { 
          type: ColorType.Solid, 
          color: isDark ? '#1E293B' : '#FFFFFF' 
        },
        textColor: isDark ? '#94A3B8' : '#64748B',
      },
      grid: {
        vertLines: { 
          color: isDark ? '#334155' : '#E2E8F0', 
          style: LineStyle.Solid 
        },
        horzLines: { 
          color: isDark ? '#334155' : '#E2E8F0', 
          style: LineStyle.Solid 
        },
      },
      crosshair: {
        mode: 0, // Normal
        vertLine: {
          width: 1,
          color: '#3B82F6',
          style: LineStyle.Dashed,
          labelBackgroundColor: '#3B82F6',
        },
        horzLine: {
          width: 1,
          color: '#3B82F6',
          style: LineStyle.Dashed,
          labelBackgroundColor: '#3B82F6',
        },
      },
      rightPriceScale: {
        borderColor: isDark ? '#334155' : '#E2E8F0',
        scaleMargins: {
          top: 0.1,
          bottom: 0.2,
        },
      },
      timeScale: {
        borderColor: isDark ? '#334155' : '#E2E8F0',
        timeVisible: true,
        secondsVisible: false,
      },
      height,
    }
  }, [theme, height])
}

export function getCandlestickColors(_theme: ChartTheme = 'dark') {
  return {
    upColor: '#26A69A',
    downColor: '#EF5350',
    borderUpColor: '#26A69A',
    borderDownColor: '#EF5350',
    wickUpColor: '#26A69A',
    wickDownColor: '#EF5350',
  }
}

export function getVolumeColors(_theme: ChartTheme = 'dark') {
  return {
    upColor: '#26A69A80',
    downColor: '#EF535080',
  }
}

