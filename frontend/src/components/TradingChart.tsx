import { useEffect, useLayoutEffect, useRef, useState, useMemo } from 'react'
import { useQuery, useQueryClient } from 'react-query'
import { RefreshCw } from 'lucide-react'
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  IChartApi,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  IPriceLine,
  LineStyle,
  SeriesMarker,
  Time,
  CandlestickData,
  LineData,
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

// Calculate Simple Moving Average
const calculateSMA = (prices: number[], period: number): number[] => {
  const sma: number[] = []
  for (let i = 0; i < prices.length; i++) {
    if (i < period - 1) {
      sma.push(NaN)
    } else {
      const sum = prices.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0)
      sma.push(sum / period)
    }
  }
  return sma
}

// Calculate Exponential Moving Average
const calculateEMA = (prices: number[], period: number): number[] => {
  const ema: number[] = []
  const multiplier = 2 / (period + 1)
  
  // Start with SMA for first value
  if (prices.length < period) {
    return prices.map(() => NaN)
  }
  
  let emaValue = prices.slice(0, period).reduce((a, b) => a + b, 0) / period
  ema.push(...new Array(period - 1).fill(NaN))
  ema.push(emaValue)
  
  // Calculate EMA for remaining values
  for (let i = period; i < prices.length; i++) {
    emaValue = (prices[i] * multiplier) + (emaValue * (1 - multiplier))
    ema.push(emaValue)
  }
  
  return ema
}


// Match TopStepX timeframes: 5s, 15s, 30s, 2m, 5m, 15m, 30m, 1h
const TIMEFRAME_OPTIONS = ['5s', '15s', '30s', '2m', '5m', '15m', '30m', '1h']

// Moving Average Configuration (TopStepX style: 4 MAs)
const MA_CONFIGS = [
  { period: 8, type: 'EMA', color: '#FFD700', label: 'MA8' },      // Yellow (fastest)
  { period: 21, type: 'EMA', color: '#EF5350', label: 'MA21' },    // Red
  { period: 50, type: 'SMA', color: '#42A5F5', label: 'MA50' },   // Light Blue
  { period: 100, type: 'SMA', color: '#26A69A', label: 'MA100' },  // Green
]

type StoredBar = {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume?: number
}

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
  const [hoveredBar, setHoveredBar] = useState<HistoricalBar | null>(null)
  const queryClient = useQueryClient()
  
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candlestickSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const maSeriesRefs = useRef<ISeriesApi<'Line'>[]>([])
  const markersPluginRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const priceLinesRef = useRef<IPriceLine[]>([])
  const barStoreRef = useRef<Record<string, StoredBar[]>>({})
  
  // Moving Average toggle
  const [showMAs, setShowMAs] = useState(true)
  
  // Chart theme configuration
  const chartTheme = useChartTheme({ theme: 'dark', height })

  // Fetch historical data - refresh periodically to get latest bars
  // Include timestamp in query key to prevent stale cache usage
  const currentTime = new Date().toISOString()
  const { data, isLoading, refetch, isRefetching } = useQuery<HistoricalDataResponse>(
    ['tradingChartData', symbol, timeframe, barLimit, currentTime],
    () =>
      analyticsApi.getHistoricalData({
        symbol,
        timeframe,
        limit: barLimit,
        end: currentTime, // Always use current time for latest data
      }),
    {
      enabled: Boolean(symbol),
      staleTime: 0, // Always consider data stale to force fresh fetches
      cacheTime: 0, // Don't cache in React Query - always fetch fresh
      refetchInterval: 60_000, // Refresh every 60 seconds to get latest historical bars
      // Real-time updates via WebSocket handle live bar updates, but we still need
      // periodic refresh to catch any missed bars or new completed bars
    }
  )
  
  // Subscribe to bar updates for this symbol/timeframe when chart is ready
  useEffect(() => {
    if (!chartInitialized || !symbol || !timeframe) {
      return
    }
    
    // Request subscription to this specific timeframe from backend
    // This ensures the bar aggregator is building bars for this timeframe
    console.log(`[TradingChart] Requesting subscription to ${symbol} ${timeframe} bars`)
    
    // Send subscription request via WebSocket
    wsService.emit('subscribe', {
      types: ['market_update'],
      symbol: symbol.toUpperCase(),
      timeframe: timeframe.toLowerCase(),
    })
    
    // Note: The bar aggregator should automatically build bars for all default timeframes
    // when quotes arrive. This subscription just ensures the specific timeframe is tracked.
  }, [chartInitialized, symbol, timeframe])

  const handleRefresh = () => {
    queryClient.invalidateQueries(['tradingChartData', symbol, timeframe, barLimit])
    refetch()
  }

  // Initialize chart
  useLayoutEffect(() => {
    const container = chartContainerRef.current
    if (!container) {
      return
    }

    container.style.position = 'relative'
    container.style.height = `${height}px`
    container.style.width = '100%'

    let chart: IChartApi | null = null
    let resizeObserver: ResizeObserver | null = null
    let handleWindowResize: (() => void) | null = null
    let destroyed = false

    const initChart = () => {
      if (destroyed || !container) {
        return
      }

      const rect = container.getBoundingClientRect()
      if (rect.width === 0 || rect.height === 0) {
        requestAnimationFrame(initChart)
        return
      }

      const getWidth = () => {
        const width = container.clientWidth || container.offsetWidth || container.parentElement?.clientWidth || 600
        return Math.max(width, 400)
      }

      const initialWidth = getWidth()

      // Format for crosshair label (bottom) - always show date + time for clarity
      const formatTimeET = (time: Time): string => {
        const date = new Date((time as number) * 1000)
        const formatterOptions: Intl.DateTimeFormatOptions = {
          timeZone: 'America/New_York',
          month: 'short',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
          hour12: false,
        }
        return date.toLocaleString('en-US', formatterOptions)
      }
      
      // Format for time axis (top) - show date only for longer timeframes to avoid clutter
      const formatTimeScaleET = (time: Time): string => {
        const date = new Date((time as number) * 1000)
        const formatterOptions: Intl.DateTimeFormatOptions = {
          timeZone: 'America/New_York',
        }
        
        const showDate = ['1h', '4h', '1d'].includes(timeframe) || 
                        (timeframe.includes('m') && parseInt(timeframe) >= 30)
        
        if (showDate) {
          formatterOptions.month = 'short'
          formatterOptions.day = 'numeric'
          formatterOptions.hour = '2-digit'
          formatterOptions.minute = '2-digit'
          formatterOptions.hour12 = false
          return date.toLocaleString('en-US', formatterOptions)
        } else {
          formatterOptions.hour = '2-digit'
          formatterOptions.minute = '2-digit'
          formatterOptions.hour12 = false
          return date.toLocaleTimeString('en-US', formatterOptions)
        }
      }

      const newChart = createChart(container, {
        ...chartTheme,
        width: initialWidth,
        height,
        timeScale: {
          ...chartTheme.timeScale,
          tickMarkFormatter: (time: Time) => formatTimeScaleET(time),
        },
        crosshair: {
          ...chartTheme.crosshair,
          vertLine: {
            ...chartTheme.crosshair?.vertLine,
            labelVisible: true,
          },
          horzLine: {
            ...chartTheme.crosshair?.horzLine,
            labelVisible: true,
          },
        },
        localization: {
          timeFormatter: (time: Time) => formatTimeET(time), // Crosshair label always shows date
        },
      })

      chart = newChart

      const candlestickSeries = newChart.addSeries(
        CandlestickSeries,
        getCandlestickColors('dark')
      )

      const volumeSeries = newChart.addSeries(HistogramSeries, {
        color: '#26A69A',
        priceFormat: {
          type: 'volume',
        },
        priceScaleId: 'volume',
      })

      newChart.priceScale('volume').applyOptions({
        scaleMargins: {
          top: 0.8,
          bottom: 0,
        },
      })

      // Initialize Moving Average line series (TopStepX style: 4 MAs)
      const maSeries: ISeriesApi<'Line'>[] = []
      
      MA_CONFIGS.forEach((config) => {
        const maLine = newChart.addSeries(LineSeries, {
          color: config.color,
          lineWidth: 1,
          title: config.label,
          priceLineVisible: false,
          lastValueVisible: false, // Hide MA values from price scale
        })
        maSeries.push(maLine)
      })

      chartRef.current = newChart
      candlestickSeriesRef.current = candlestickSeries
      volumeSeriesRef.current = volumeSeries
      maSeriesRefs.current = maSeries
      markersPluginRef.current = createSeriesMarkers(candlestickSeries, [])
      setChartInitialized(true)

      resizeObserver = new ResizeObserver((entries) => {
        if (!entries.length || !chartRef.current) return
        const { width } = entries[0].contentRect
        const newWidth = Math.max(width, 400)
        chartRef.current.applyOptions({ width: newWidth })
      })
      resizeObserver.observe(container)

      handleWindowResize = () => {
        if (chartRef.current && container) {
          const newWidth = Math.max(container.clientWidth || 600, 400)
          chartRef.current.applyOptions({ width: newWidth })
        }
      }
      window.addEventListener('resize', handleWindowResize)
    }

    const rafId = requestAnimationFrame(initChart)

    return () => {
      destroyed = true
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
  }, [height, chartTheme]) // Chart initialization - timeframe changes handled separately

  // Memoize chart data to prevent unnecessary recalculations
  const chartData = useMemo(() => {
    if (!data?.bars || data.bars.length === 0) return null

    const volumeColors = getVolumeColors('dark')
    const sorted = [...data.bars]
      .map((bar: HistoricalBar) => ({
        time: toUnixTimestamp(bar.timestamp),
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
        volume: bar.volume,
      }))
      .sort((a, b) => (a.time as number) - (b.time as number))

    let lastTime: number | null = null
    const candlestickData: CandlestickData<Time>[] = []
    const volumeData: { time: Time; value: number; color: string }[] = []

    for (const bar of sorted) {
      const t = bar.time as number
      if (lastTime !== null && t === lastTime) {
        candlestickData[candlestickData.length - 1] = {
          time: bar.time,
          open: bar.open,
          high: bar.high,
          low: bar.low,
          close: bar.close,
        }
        volumeData[volumeData.length - 1] = {
          time: bar.time,
          value: bar.volume,
          color: bar.close >= bar.open ? volumeColors.upColor : volumeColors.downColor,
        }
        continue
      }
      candlestickData.push({
        time: bar.time,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      })
      volumeData.push({
        time: bar.time,
        value: bar.volume,
        color: bar.close >= bar.open ? volumeColors.upColor : volumeColors.downColor,
      })
      lastTime = t
    }

    return { candlestickData, volumeData }
  }, [data])

  // Calculate Moving Averages from chart data
  const maData = useMemo(() => {
    if (!chartData || !chartData.candlestickData.length) return null

    const closes = chartData.candlestickData.map(bar => bar.close)
    const times = chartData.candlestickData.map(bar => bar.time)
    
    return MA_CONFIGS.map(config => {
      const maValues = config.type === 'EMA' 
        ? calculateEMA(closes, config.period)
        : calculateSMA(closes, config.period)
      
      const maLineData: LineData<Time>[] = times.map((time, idx) => ({
        time,
        value: maValues[idx],
      })).filter(point => !isNaN(point.value as number))

      return { ...config, data: maLineData }
    })
  }, [chartData])

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

      // Update Moving Averages
      if (showMAs && maData && maSeriesRefs.current.length === maData.length) {
        maData.forEach((ma, idx) => {
          if (maSeriesRefs.current[idx]) {
            maSeriesRefs.current[idx].setData(ma.data)
          }
        })
      } else if (!showMAs) {
        // Clear MA data when hidden
        maSeriesRefs.current.forEach(series => {
          series.setData([])
        })
      }

      const storeKey = `${symbol.toUpperCase()}:${timeframe.toLowerCase()}`
      barStoreRef.current[storeKey] = chartData.candlestickData.map((bar, idx) => ({
        time: Number(bar.time),
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
        volume: chartData.volumeData[idx]?.value ?? 0,
      }))

      if (chartRef.current) {
        chartRef.current.timeScale().fitContent()
      }
      console.log('[TradingChart] Chart data updated successfully')
    } catch (error) {
      console.error('[TradingChart] Error updating chart data:', error)
    }
  }, [chartData, chartInitialized, symbol, timeframe, barLimit, showMAs, maData])

  // Update timeScale formatter when timeframe changes (without recreating chart)
  useEffect(() => {
    if (!chartRef.current || !chartInitialized) return

    // Format for crosshair label (bottom) - always show date + time
    const formatTimeET = (time: Time): string => {
      const date = new Date((time as number) * 1000)
      const formatterOptions: Intl.DateTimeFormatOptions = {
        timeZone: 'America/New_York',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }
      return date.toLocaleString('en-US', formatterOptions)
    }
    
    // Format for time axis (top) - show date only for longer timeframes
    const formatTimeScaleET = (time: Time): string => {
      const date = new Date((time as number) * 1000)
      const formatterOptions: Intl.DateTimeFormatOptions = {
        timeZone: 'America/New_York',
      }
      
      const showDate = ['1h', '4h', '1d'].includes(timeframe) || 
                      (timeframe.includes('m') && parseInt(timeframe) >= 30)
      
      if (showDate) {
        formatterOptions.month = 'short'
        formatterOptions.day = 'numeric'
        formatterOptions.hour = '2-digit'
        formatterOptions.minute = '2-digit'
        formatterOptions.hour12 = false
        return date.toLocaleString('en-US', formatterOptions)
      } else {
        formatterOptions.hour = '2-digit'
        formatterOptions.minute = '2-digit'
        formatterOptions.hour12 = false
        return date.toLocaleTimeString('en-US', formatterOptions)
      }
    }

    // Update timeScale formatter (top axis)
    const timeScale = chartRef.current.timeScale()
    timeScale.applyOptions({
      tickMarkFormatter: (time: Time, _tickMarkType: any, _locale: string) => formatTimeScaleET(time),
    } as any)

    // Update localization formatter (crosshair label at bottom - always show date)
    chartRef.current.applyOptions({
      localization: {
        timeFormatter: (time: Time) => formatTimeET(time),
      },
    } as any)
  }, [timeframe, chartInitialized])

  // Subscribe to crosshair move events for OHLC display (after chart and data are ready)
  useEffect(() => {
    if (!chartRef.current || !chartInitialized || !candlestickSeriesRef.current || !data?.bars) {
      return
    }

    const chart = chartRef.current
    const candlestickSeries = candlestickSeriesRef.current

    const handleCrosshairMove = (param: any) => {
      if (param.time && param.seriesData) {
        const candlestickData = param.seriesData.get(candlestickSeries) as CandlestickData<Time> | undefined
        if (candlestickData) {
          // Find the corresponding bar from data
          const barTime = Number(param.time)
          const matchingBar = data.bars.find((bar: HistoricalBar) => {
            const barTimestamp = Number(toUnixTimestamp(bar.timestamp))
            return barTimestamp === barTime
          })
          if (matchingBar) {
            setHoveredBar(matchingBar)
          } else {
            // If no exact match, use the candlestick data directly
            setHoveredBar({
              timestamp: new Date((barTime as number) * 1000).toISOString(),
              open: candlestickData.open,
              high: candlestickData.high,
              low: candlestickData.low,
              close: candlestickData.close,
              volume: 0,
            } as HistoricalBar)
          }
        } else {
          setHoveredBar(null)
        }
      } else {
        setHoveredBar(null)
      }
    }

    chart.subscribeCrosshairMove(handleCrosshairMove)

    return () => {
      // Note: lightweight-charts doesn't have unsubscribe, but cleanup is handled by chart removal
    }
  }, [chartInitialized, data])

  // Memoize relevant positions to prevent unnecessary recalculations
  const relevantPositions = useMemo(() => {
    const filtered = positions.filter((pos) => {
      const matchesSymbol = pos.symbol === symbol
      // For chart display, we can use current time if no timestamp exists
      // This allows showing positions even without historical timestamp
      return matchesSymbol
    })
    console.log('[TradingChart] Filtered positions', { 
      total: positions.length, 
      filtered: filtered.length,
      symbol,
      positions: filtered.map(p => ({ symbol: p.symbol, timestamp: p.timestamp }))
    })
    return filtered
  }, [positions, symbol])

  // Memoize position markers
  const positionMarkers = useMemo(() => {
    if (!showPositions || relevantPositions.length === 0) return []

    return relevantPositions.map((pos) => {
      const isLong = pos.side === 'LONG'
      // Try multiple timestamp fields, fallback to current time for display
      let timestamp = pos.timestamp || pos.opened_at || pos.created_at
      if (!timestamp) {
        // If no timestamp, use current time (position is active now)
        timestamp = new Date().toISOString()
        console.log('[TradingChart] Position missing timestamp, using current time', { 
          symbol: pos.symbol, 
          side: pos.side 
        })
      }
      const time = toUnixTimestamp(timestamp)

      return {
        time,
        position: isLong ? 'belowBar' : 'aboveBar',
        color: isLong ? '#26A69A' : '#EF5350',
        shape: isLong ? 'arrowUp' : 'arrowDown',
        text: `${pos.side} ${pos.quantity}@${pos.entry_price.toFixed(2)}`,
        size: 1,
      } as SeriesMarker<Time>
    }).filter((m): m is SeriesMarker<Time> => m !== null)
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
      return
    }

    const normalizedSymbol = symbol.toUpperCase()

    const applyUpdate = (rawUpdate: any) => {
      if (!rawUpdate || !candlestickSeriesRef.current || !volumeSeriesRef.current) {
        return
      }
      const updateSymbol = (rawUpdate.symbol || '').toUpperCase()
      if (updateSymbol !== normalizedSymbol) {
        return
      }
      // Accept updates for the current timeframe (case-insensitive)
      // Also accept updates without timeframe if they match the symbol
      const updateTimeframe = (rawUpdate.timeframe || '').toLowerCase()
      const currentTimeframe = timeframe.toLowerCase()
      
      // If timeframe is specified and doesn't match, skip
      if (updateTimeframe && updateTimeframe !== currentTimeframe) {
        return
      }
      // If no timeframe specified, accept it (might be a general update)
      const barData = rawUpdate.bar
      if (!barData) return

      const timestamp = rawUpdate.timestamp || rawUpdate.bar?.timestamp || new Date().toISOString()
      const time = Number(toUnixTimestamp(timestamp))
      const storeKey = `${normalizedSymbol}:${updateTimeframe}`
      const store = barStoreRef.current[storeKey] ? [...barStoreRef.current[storeKey]] : []
      const newEntry: StoredBar = {
        time,
        open: barData.open,
        high: barData.high,
        low: barData.low,
        close: barData.close,
        volume: barData.volume,
      }
      const last = store[store.length - 1]
      if (!last || time >= last.time) {
        if (last && time === last.time) {
          store[store.length - 1] = newEntry
        } else {
          store.push(newEntry)
        }
      } else {
        // Out-of-order update: replace matching bar if found
        const idx = store.findIndex((item) => item.time === time)
        if (idx >= 0) {
          store[idx] = newEntry
        } else {
          store.push(newEntry)
          store.sort((a, b) => a.time - b.time)
        }
      }
      // Enforce max size to avoid unbounded memory
      const maxBars = Math.max(barLimit, 100)
      if (store.length > maxBars + 20) {
        store.splice(0, store.length - (maxBars + 20))
      }
      barStoreRef.current[storeKey] = store

      const updateTime = time as UTCTimestamp
      candlestickSeriesRef.current.update({
        time: updateTime,
        open: barData.open,
        high: barData.high,
        low: barData.low,
        close: barData.close,
      })

      if (barData.volume !== undefined) {
        const volumeColors = getVolumeColors('dark')
        volumeSeriesRef.current.update({
          time: updateTime,
          value: barData.volume,
          color: barData.close >= barData.open ? volumeColors.upColor : volumeColors.downColor,
        })
      }
    }

    const handleMessage = (payload: any) => {
      if (!payload) return
      const raw = payload.data ?? payload
      const updates = Array.isArray(raw) ? raw : [raw]
      updates.forEach(applyUpdate)
    }

    wsService.on('market_update', handleMessage)
    wsService.on('market_update_batch', handleMessage)

    return () => {
      wsService.off('market_update', handleMessage)
      wsService.off('market_update_batch', handleMessage)
    }
  }, [symbol, timeframe, chartInitialized, barLimit])

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
            <button
              onClick={handleRefresh}
              disabled={isRefetching}
              className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 disabled:opacity-50 text-white text-xs font-semibold rounded transition-colors flex items-center gap-2"
              title="Refresh chart data"
            >
              <RefreshCw className={`w-4 h-4 ${isRefetching ? 'animate-spin' : ''}`} />
              Refresh
            </button>
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
                checked={showMAs}
                onChange={(e) => setShowMAs(e.target.checked)}
                className="rounded"
              />
              Moving Averages
            </label>
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
      <div className="relative rounded overflow-hidden" style={{ minHeight: height }}>
        <div ref={chartContainerRef} className="rounded overflow-hidden" style={{ minHeight: height }} />
        
        {/* OHLC Display - Top Left Overlay (TopStepX Style) - Shows bar under crosshair */}
        {data && data.bars.length > 0 && chartInitialized && (() => {
          // Use hovered bar if crosshair is over a bar, otherwise use latest bar
          const displayBar = hoveredBar || data.bars[data.bars.length - 1]
          const change = displayBar.close - displayBar.open
          const changePercentValue = (change / displayBar.open) * 100
          const changePercent = changePercentValue.toFixed(2)
          
          return (
            <div className="absolute top-2 left-2 z-10 bg-slate-900/90 backdrop-blur-sm rounded px-3 py-2 text-xs font-mono border border-slate-700">
              <div className="flex items-center gap-4">
                <span className="text-slate-400">
                  <span className="text-slate-500">O</span>{' '}
                  <span className="text-slate-300">{displayBar.open.toFixed(2)}</span>
                </span>
                <span className="text-slate-400">
                  <span className="text-slate-500">H</span>{' '}
                  <span className="text-green-400">{displayBar.high.toFixed(2)}</span>
                </span>
                <span className="text-slate-400">
                  <span className="text-slate-500">L</span>{' '}
                  <span className="text-red-400">{displayBar.low.toFixed(2)}</span>
                </span>
                <span className="text-slate-400">
                  <span className="text-slate-500">C</span>{' '}
                  <span className={change >= 0 ? 'text-green-400' : 'text-red-400'}>
                    {displayBar.close.toFixed(2)}
                  </span>
                </span>
                <span className={`ml-2 ${change >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {change >= 0 ? '+' : ''}{change.toFixed(2)} ({changePercentValue >= 0 ? '+' : ''}{changePercent}%)
                </span>
              </div>
            </div>
          )
        })()}

        {/* Live Data Indicator - Green Circle (TopStepX Style) */}
        {chartInitialized && (
          <div className="absolute top-2 right-2 z-10 flex items-center gap-2 bg-slate-900/90 backdrop-blur-sm rounded px-2 py-1 border border-slate-700">
            <div className="w-2 h-2 bg-green-400 rounded-full animate-pulse" title="Live data" />
            <span className="text-xs text-slate-400">LIVE</span>
          </div>
        )}

        {(!data || data.bars.length === 0) && !isLoading && (
          <div className="absolute inset-0 flex items-center justify-center text-slate-500 text-sm bg-slate-900/70">
            No data available for {symbol}
          </div>
        )}
        {(isLoading || !chartInitialized) && (
          <div className="absolute inset-0 flex items-center justify-center text-slate-400 text-sm bg-slate-900/70">
            <div className="flex flex-col items-center gap-2">
              <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin"></div>
              <span>Loading chart data...</span>
            </div>
          </div>
        )}
      </div>

      {/* Chart Info Footer */}
      {data && data.bars.length > 0 && (
        <div className="flex items-center justify-between text-xs text-slate-400 bg-slate-900/60 rounded px-4 py-2">
          <div className="text-slate-500">
            {positions.filter(p => p.symbol === symbol).length} position(s) • {orders.filter(o => o.symbol === symbol && o.status === 'PENDING').length} order(s)
          </div>
          <div className="text-slate-500">
            {data.bars.length} bars • {timeframe} timeframe
          </div>
        </div>
      )}
    </div>
  )
}

