import { useEffect, useRef, useState, useMemo } from 'react'
import { useQuery } from 'react-query'
import {
  CandlestickSeries,
  HistogramSeries,
  IChartApi,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  IPriceLine,
  LineStyle,
  SeriesMarker,
  Time,
  CandlestickData,
  UTCTimestamp,
  createChart,
  createSeriesMarkers,
} from 'lightweight-charts'
import { analyticsApi } from '../services/api'
import { wsService } from '../services/websocket'
import type { HistoricalBar, HistoricalDataResponse, Position, Order } from '../types'
import { useChartTheme, getCandlestickColors, getVolumeColors } from '../hooks/useChartTheme'

// Convert timestamp to Unix seconds (UTC)
// Note: Chart displays times in UTC, so we keep timestamps as-is
// If you need local time display, adjust the timeScale formatter instead
const toUnixTimestamp = (timestamp: string): UTCTimestamp =>
  Math.floor(new Date(timestamp).getTime() / 1000) as UTCTimestamp


const TIMEFRAME_OPTIONS = ['1m', '5m', '15m', '1h', '4h', '1d']

interface TradingChartProps {
  symbol?: string
  positions?: Position[]
  orders?: Order[]
  height?: number
  showPositions?: boolean
  showOrders?: boolean
}

export default function TradingChart({
  symbol: propSymbol,
  positions = [],
  orders = [],
  height = 500,
  showPositions: propShowPositions = true,
  showOrders: propShowOrders = true,
}: TradingChartProps) {
  const [symbol, setSymbol] = useState(propSymbol || 'MNQ')
  const [timeframe, setTimeframe] = useState('5m')
  const [barLimit, setBarLimit] = useState(300)
  const [showPositions, setShowPositions] = useState(propShowPositions)
  const [showOrders, setShowOrders] = useState(propShowOrders)
  const [chartInitialized, setChartInitialized] = useState(false)
  
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candlestickSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const markersPluginRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const priceLinesRef = useRef<IPriceLine[]>([])
  
  // Chart theme configuration
  const chartTheme = useChartTheme({ theme: 'dark', height })

  // Fetch historical data
  const { data, isLoading } = useQuery<HistoricalDataResponse>(
    ['tradingChartData', symbol, timeframe, barLimit],
    () =>
      analyticsApi.getHistoricalData({
        symbol,
        timeframe,
        limit: barLimit,
        end: new Date().toISOString(),
      }),
    {
      enabled: Boolean(symbol),
      staleTime: 30_000,
      refetchInterval: 30_000, // Refresh every 30s
    }
  )

  // Initialize chart
  useEffect(() => {
    if (!chartContainerRef.current) {
      console.log('[TradingChart] No container ref')
      return
    }
    const container = chartContainerRef.current

    // Ensure container has explicit dimensions
    container.style.position = 'relative'
    container.style.height = `${height}px`
    container.style.width = '100%'

    let chart: IChartApi | null = null
    let resizeObserver: ResizeObserver | null = null
    let handleWindowResize: (() => void) | null = null

    // Wait for next frame to ensure container is rendered and visible
    const initChart = () => {
      if (!container) {
        console.log('[TradingChart] Container is null in initChart')
        return
      }

      // Check if container is visible
      const rect = container.getBoundingClientRect()
      if (rect.width === 0 || rect.height === 0) {
        console.log('[TradingChart] Container has zero dimensions, retrying...', { width: rect.width, height: rect.height })
        // Retry after a short delay
        setTimeout(initChart, 100)
        return
      }

      console.log('[TradingChart] Initializing chart', { width: rect.width, height: rect.height })

      // Get initial width, with fallback and minimum
      const getWidth = () => {
        const width = container.clientWidth || container.offsetWidth || container.parentElement?.clientWidth || 600
        return Math.max(width, 400) // Minimum 400px
      }

      const initialWidth = getWidth()

      // Helper function to format time in ET
      const formatTimeET = (time: Time): string => {
        const date = new Date((time as number) * 1000)
        const formatterOptions: Intl.DateTimeFormatOptions = {
          timeZone: 'America/New_York', // Automatically handles EST/EDT
        }
        
        if (timeframe === '1d') {
          formatterOptions.month = 'short'
          formatterOptions.day = 'numeric'
          return date.toLocaleDateString('en-US', formatterOptions)
        } else {
          formatterOptions.hour = '2-digit'
          formatterOptions.minute = '2-digit'
          formatterOptions.hour12 = false
          return date.toLocaleTimeString('en-US', formatterOptions)
        }
      }

      chart = createChart(container, {
        ...chartTheme,
        width: initialWidth,
        height,
        timeScale: {
          ...chartTheme.timeScale,
          // Custom formatter to display times in ET (UTC-5 or UTC-4)
          tickMarkFormatter: (time: Time, _tickMarkType: any, _locale: string) => {
            return formatTimeET(time)
          },
        },
        crosshair: {
          ...chartTheme.crosshair,
          // Format crosshair time in ET timezone
          vertLine: {
            ...chartTheme.crosshair?.vertLine,
            labelVisible: true,
          },
          horzLine: {
            ...chartTheme.crosshair?.horzLine,
            labelVisible: true,
          },
        },
        // Use localization to format crosshair time in ET
        localization: {
          timeFormatter: (time: Time) => {
            return formatTimeET(time)
          },
        },
      })

      // Add candlestick series
      const candlestickSeries = chart.addSeries(
        CandlestickSeries,
        getCandlestickColors('dark')
      )

      // Add volume series
      const volumeSeries = chart.addSeries(HistogramSeries, {
        color: '#26A69A',
        priceFormat: {
          type: 'volume',
        },
        priceScaleId: 'volume',
      })

      chart.priceScale('volume').applyOptions({
        scaleMargins: {
          top: 0.8,
          bottom: 0,
        },
      })

      chartRef.current = chart
      candlestickSeriesRef.current = candlestickSeries
      volumeSeriesRef.current = volumeSeries
      markersPluginRef.current = createSeriesMarkers(candlestickSeries, [])
      setChartInitialized(true)
      console.log('[TradingChart] Chart initialized successfully')

      // If data is already available, set it immediately
      // This prevents the chart from waiting for the data update effect
      if (data?.bars && data.bars.length > 0) {
        console.log('[TradingChart] Setting initial data', { barCount: data.bars.length })
        try {
          const candlestickData: CandlestickData<Time>[] = data.bars
            .map((bar: HistoricalBar) => ({
              time: toUnixTimestamp(bar.timestamp),
              open: bar.open,
              high: bar.high,
              low: bar.low,
              close: bar.close,
            }))
            .sort((a, b) => (a.time as number) - (b.time as number))

          const volumeColors = getVolumeColors('dark')
          const volumeData = data.bars
            .map((bar: HistoricalBar) => ({
              time: toUnixTimestamp(bar.timestamp),
              value: bar.volume,
              color: bar.close >= bar.open ? volumeColors.upColor : volumeColors.downColor,
            }))
            .sort((a, b) => (a.time as number) - (b.time as number))

          candlestickSeries.setData(candlestickData)
          volumeSeries.setData(volumeData)
          chart.timeScale().fitContent()
          console.log('[TradingChart] Initial data set successfully')
        } catch (error) {
          console.error('[TradingChart] Error setting initial chart data:', error)
        }
      } else {
        console.log('[TradingChart] No initial data available')
      }

      resizeObserver = new ResizeObserver((entries) => {
        if (!entries.length || !chartRef.current) return
        const { width } = entries[0].contentRect
        const newWidth = Math.max(width, 400) // Minimum 400px
        chartRef.current.applyOptions({ width: newWidth })
      })
      resizeObserver.observe(container)

      // Also handle window resize as fallback
      handleWindowResize = () => {
        if (chartRef.current && container) {
          const newWidth = Math.max(container.clientWidth || 600, 400)
          chartRef.current.applyOptions({ width: newWidth })
        }
      }
      window.addEventListener('resize', handleWindowResize)
    }

    // Use requestAnimationFrame to ensure DOM is ready
    const rafId = requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        initChart()
      })
    })

    return () => {
      console.log('[TradingChart] Cleaning up chart')
      cancelAnimationFrame(rafId)
      if (handleWindowResize) {
        window.removeEventListener('resize', handleWindowResize)
      }
      resizeObserver?.disconnect()
      markersPluginRef.current?.detach()
      markersPluginRef.current = null
      if (chart) {
        chart.remove()
      }
      setChartInitialized(false)
    }
  }, [height, chartTheme, timeframe]) // Chart initialization - data is handled by separate effect

  // Memoize chart data to prevent unnecessary recalculations
  const chartData = useMemo(() => {
    if (!data?.bars || data.bars.length === 0) return null

    const candlestickData: CandlestickData<Time>[] = data.bars
      .map((bar: HistoricalBar) => ({
        time: toUnixTimestamp(bar.timestamp),
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      }))
      .sort((a, b) => (a.time as number) - (b.time as number))

    const volumeColors = getVolumeColors('dark')
    const volumeData = data.bars
      .map((bar: HistoricalBar) => ({
        time: toUnixTimestamp(bar.timestamp),
        value: bar.volume,
        color: bar.close >= bar.open ? volumeColors.upColor : volumeColors.downColor,
      }))
      .sort((a, b) => (a.time as number) - (b.time as number))

    return { candlestickData, volumeData }
  }, [data])

  // Update chart data when data changes (including timeframe/barLimit changes)
  useEffect(() => {
    if (!chartInitialized) {
      return
    }
    if (!chartData || !candlestickSeriesRef.current || !volumeSeriesRef.current) {
      return
    }

    console.log('[TradingChart] Updating chart data', { 
      barCount: chartData.candlestickData.length, 
      symbol, 
      timeframe, 
      barLimit 
    })

    try {
      candlestickSeriesRef.current.setData(chartData.candlestickData)
      volumeSeriesRef.current.setData(chartData.volumeData)

      // Fit content
      if (chartRef.current) {
        chartRef.current.timeScale().fitContent()
      }
      console.log('[TradingChart] Chart data updated successfully')
    } catch (error) {
      console.error('[TradingChart] Error updating chart data:', error)
    }
  }, [chartData, chartInitialized])

  // Memoize relevant positions to prevent unnecessary recalculations
  const relevantPositions = useMemo(() => {
    return positions.filter((pos) => pos.symbol === symbol && pos.timestamp)
  }, [positions, symbol])

  // Memoize position markers
  const positionMarkers = useMemo(() => {
    if (!showPositions || relevantPositions.length === 0) return []

    return relevantPositions.map((pos) => {
      const isLong = pos.side === 'LONG'
      const time = toUnixTimestamp(pos.timestamp!)

      return {
        time,
        position: isLong ? 'belowBar' : 'aboveBar',
        color: isLong ? '#26A69A' : '#EF5350',
        shape: isLong ? 'arrowUp' : 'arrowDown',
        text: `${pos.side} ${pos.quantity}@${pos.entry_price.toFixed(2)}`,
        size: 1,
      } as SeriesMarker<Time>
    })
  }, [relevantPositions, showPositions])

  // Add position markers
  useEffect(() => {
    if (!chartInitialized || !markersPluginRef.current) return

    console.log('[TradingChart] Updating position markers', { 
      markerCount: positionMarkers.length,
      symbol 
    })

    markersPluginRef.current.setMarkers(positionMarkers)
  }, [positionMarkers, chartInitialized, symbol])

  // Memoize relevant orders to prevent unnecessary recalculations
  const relevantOrders = useMemo(() => {
    return orders.filter(
      (order) => order.symbol === symbol && order.status === 'PENDING' && order.price
    )
  }, [orders, symbol])

  // Add order price lines
  useEffect(() => {
    if (!chartInitialized || !candlestickSeriesRef.current) return

    // Remove existing price lines
    priceLinesRef.current.forEach((line) => {
      try {
        candlestickSeriesRef.current?.removePriceLine(line)
      } catch (error) {
        console.warn('[TradingChart] Failed to remove price line', error)
      }
    })
    priceLinesRef.current = []

    if (!showOrders || relevantOrders.length === 0) {
      return
    }

    console.log('[TradingChart] Updating order price lines', { 
      relevantOrders: relevantOrders.length,
      symbol 
    })

    relevantOrders.forEach((order) => {
      if (!candlestickSeriesRef.current) return

      const isLongOrder = order.side === 'BUY'

      try {
        const line = candlestickSeriesRef.current.createPriceLine({
          price: order.price!,
          color: isLongOrder ? '#10B981' : '#F59E0B',
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: `${order.side} ${order.quantity}`,
        })
        priceLinesRef.current.push(line)
      } catch (error) {
        console.error('[TradingChart] Error creating price line:', error)
      }
    })
  }, [relevantOrders, showOrders, chartInitialized])

  // WebSocket integration for live updates
  useEffect(() => {
    if (!chartInitialized || !symbol) {
      console.log('[TradingChart] Skipping WebSocket setup', { chartInitialized, symbol })
      return
    }

    console.log('[TradingChart] Setting up WebSocket listener for', symbol)

    const handleMarketUpdate = (data: any) => {
      if (data.symbol !== symbol) {
        console.log('[TradingChart] Market update for different symbol', { 
          updateSymbol: data.symbol, 
          chartSymbol: symbol 
        })
        return
      }
      if (!candlestickSeriesRef.current || !volumeSeriesRef.current) {
        console.log('[TradingChart] Missing series refs for market update')
        return
      }

      try {
        // Update the last candle with new data
        const time = toUnixTimestamp(data.timestamp)

        if (data.bar) {
          console.log('[TradingChart] Updating candle from WebSocket', { time, close: data.bar.close })
          candlestickSeriesRef.current.update({
            time,
            open: data.bar.open,
            high: data.bar.high,
            low: data.bar.low,
            close: data.bar.close,
          })

          if (data.bar.volume) {
            const volumeColors = getVolumeColors('dark')
            volumeSeriesRef.current.update({
              time,
              value: data.bar.volume,
              color: data.bar.close >= data.bar.open ? volumeColors.upColor : volumeColors.downColor,
            })
          }
        }
      } catch (error) {
        console.error('[TradingChart] Error updating live chart data:', error)
      }
    }

    wsService.on('market_update', handleMarketUpdate)

    return () => {
      console.log('[TradingChart] Removing WebSocket listener')
      wsService.off('market_update', handleMarketUpdate)
    }
  }, [symbol, chartInitialized])

  const BAR_LIMITS = [
    { value: 100, label: '100' },
    { value: 300, label: '300' },
    { value: 500, label: '500' },
    { value: 1000, label: '1000' },
  ]

  return (
    <div className="bg-slate-800 rounded-lg p-6 border border-slate-700 space-y-4">
      {/* Chart Controls */}
      <div className="flex flex-col gap-3">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
          <h2 className="text-xl font-semibold text-slate-100">
            {symbol} Price Chart
          </h2>
          <div className="flex items-center gap-3 flex-wrap">
            <input
              value={symbol}
              onChange={(event) => setSymbol(event.target.value.toUpperCase())}
              className="bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="Symbol"
              maxLength={10}
            />
            <div className="flex gap-1">
              {TIMEFRAME_OPTIONS.map((option) => (
                <button
                  key={option}
                  onClick={() => setTimeframe(option)}
                  className={`px-3 py-1.5 text-xs font-semibold rounded transition-colors ${
                    timeframe === option
                      ? 'bg-blue-600 text-white'
                      : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                  }`}
                >
                  {option.toUpperCase()}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Bar limit controls */}
        <div className="flex items-center gap-3 text-sm flex-wrap">
          <span className="text-slate-400">Bars:</span>
          <div className="flex gap-1">
            {BAR_LIMITS.map((limit) => (
              <button
                key={limit.value}
                onClick={() => setBarLimit(limit.value)}
                className={`px-3 py-1 text-xs font-semibold rounded transition-colors ${
                  barLimit === limit.value
                    ? 'bg-green-600 text-white'
                    : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                }`}
              >
                {limit.label}
              </button>
            ))}
          </div>

          {/* Toggle switches */}
          <div className="flex items-center gap-4 ml-auto">
            <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
              <input
                type="checkbox"
                checked={showPositions}
                onChange={(e) => setShowPositions(e.target.checked)}
                className="rounded"
              />
              Show Positions
            </label>
            <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
              <input
                type="checkbox"
                checked={showOrders}
                onChange={(e) => setShowOrders(e.target.checked)}
                className="rounded"
              />
              Show Orders
            </label>
          </div>
        </div>
      </div>

      {/* Chart Container */}
      {isLoading ? (
        <div
          className="flex items-center justify-center text-slate-400 text-sm bg-slate-900/50 rounded"
          style={{ height }}
        >
          <div className="flex flex-col items-center gap-2">
            <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin"></div>
            <span>Loading chart data...</span>
          </div>
        </div>
      ) : !data || data.bars.length === 0 ? (
        <div
          className="flex items-center justify-center text-slate-500 text-sm bg-slate-900/50 rounded"
          style={{ height }}
        >
          No data available for {symbol}
        </div>
      ) : (
        <div ref={chartContainerRef} className="rounded overflow-hidden" style={{ minHeight: height }} />
      )}

      {/* Chart Legend/Info */}
      {data && data.bars.length > 0 && (
        <div className="flex items-center justify-between text-xs text-slate-400 bg-slate-900/60 rounded px-4 py-2">
          <div className="flex items-center gap-4">
            <span>
              <span className="text-slate-500">O:</span>{' '}
              <span className="text-slate-300">{data.bars[data.bars.length - 1].open.toFixed(2)}</span>
            </span>
            <span>
              <span className="text-slate-500">H:</span>{' '}
              <span className="text-green-400">{data.bars[data.bars.length - 1].high.toFixed(2)}</span>
            </span>
            <span>
              <span className="text-slate-500">L:</span>{' '}
              <span className="text-red-400">{data.bars[data.bars.length - 1].low.toFixed(2)}</span>
            </span>
            <span>
              <span className="text-slate-500">C:</span>{' '}
              <span className={data.bars[data.bars.length - 1].close >= data.bars[data.bars.length - 1].open ? 'text-green-400' : 'text-red-400'}>
                {data.bars[data.bars.length - 1].close.toFixed(2)}
              </span>
            </span>
          </div>
          <div className="text-slate-500">
            {positions.filter(p => p.symbol === symbol).length} position(s) • {orders.filter(o => o.symbol === symbol && o.status === 'PENDING').length} order(s)
          </div>
        </div>
      )}
    </div>
  )
}

