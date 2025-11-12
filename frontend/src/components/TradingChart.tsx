import { useEffect, useRef, useState } from 'react'
import { useQuery } from 'react-query'
import {
  createChart,
  IChartApi,
  LineStyle,
  SeriesMarker,
  Time,
  CandlestickData,
} from 'lightweight-charts'
import { analyticsApi } from '../services/api'
import { wsService } from '../services/websocket'
import type { HistoricalBar, HistoricalDataResponse, Position, Order } from '../types'
import { useChartTheme, getCandlestickColors, getVolumeColors } from '../hooks/useChartTheme'

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
  showPositions = true,
  showOrders = true,
}: TradingChartProps) {
  const [symbol, setSymbol] = useState(propSymbol || 'MNQ')
  const [timeframe, setTimeframe] = useState('5m')
  const [barLimit, setBarLimit] = useState(300)
  
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candlestickSeriesRef = useRef<any>(null)
  const volumeSeriesRef = useRef<any>(null)
  
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
    if (!chartContainerRef.current) return

    const chart = createChart(chartContainerRef.current, {
      ...chartTheme,
      width: chartContainerRef.current.clientWidth,
    })

    // Add candlestick series
    const candlestickSeries = chart.addCandlestickSeries(getCandlestickColors('dark'))

    // Add volume series
    const volumeSeries = chart.addHistogramSeries({
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

    // Handle resize
    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        chartRef.current.applyOptions({
          width: chartContainerRef.current.clientWidth,
        })
      }
    }

    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
    }
  }, [height])

  // Update chart data
  useEffect(() => {
    if (!data?.bars || !candlestickSeriesRef.current || !volumeSeriesRef.current) return

    try {
      const candlestickData: CandlestickData<Time>[] = data.bars.map((bar: HistoricalBar) => ({
        time: (new Date(bar.timestamp).getTime() / 1000) as Time,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      }))

      const volumeColors = getVolumeColors('dark')
      const volumeData = data.bars.map((bar: HistoricalBar) => ({
        time: (new Date(bar.timestamp).getTime() / 1000) as Time,
        value: bar.volume,
        color: bar.close >= bar.open ? volumeColors.upColor : volumeColors.downColor,
      }))

      candlestickSeriesRef.current.setData(candlestickData)
      volumeSeriesRef.current.setData(volumeData)

      // Fit content
      if (chartRef.current) {
        chartRef.current.timeScale().fitContent()
      }
    } catch (error) {
      console.error('Error updating chart data:', error)
    }
  }, [data])

  // Add position markers
  useEffect(() => {
    if (!candlestickSeriesRef.current || !showPositions) return

    const markers: SeriesMarker<Time>[] = positions
      .filter((pos) => pos.symbol === symbol && pos.timestamp)
      .map((pos) => {
        const isLong = pos.side === 'LONG'
        const time = (new Date(pos.timestamp!).getTime() / 1000) as Time

        return {
          time,
          position: isLong ? 'belowBar' : 'aboveBar',
          color: isLong ? '#26A69A' : '#EF5350',
          shape: isLong ? 'arrowUp' : 'arrowDown',
          text: `${pos.side} ${pos.quantity}@${pos.entry_price.toFixed(2)}`,
          size: 1,
        } as SeriesMarker<Time>
      })

    if (markers.length > 0 && candlestickSeriesRef.current) {
      candlestickSeriesRef.current.setMarkers(markers)
    }
  }, [positions, symbol, showPositions])

  // Add order price lines
  useEffect(() => {
    if (!candlestickSeriesRef.current || !showOrders) return

    // Remove old price lines
    // Note: Lightweight Charts doesn't have a built-in way to remove all price lines
    // so we'll create new ones when needed

      orders
      .filter((order) => order.symbol === symbol && order.status === 'PENDING' && order.price)
      .forEach((order) => {
        if (!candlestickSeriesRef.current) return

        const isLongOrder = order.side === 'BUY'

        try {
          candlestickSeriesRef.current.createPriceLine({
            price: order.price!,
            color: isLongOrder ? '#10B981' : '#F59E0B',
            lineWidth: 2,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: `${order.side} ${order.quantity}`,
          })
        } catch (error) {
          console.error('Error creating price line:', error)
        }
      })
  }, [orders, symbol, showOrders])

  // WebSocket integration for live updates
  useEffect(() => {
    if (!symbol) return

    const handleMarketUpdate = (data: any) => {
      if (data.symbol !== symbol) return
      if (!candlestickSeriesRef.current || !volumeSeriesRef.current) return

      try {
        // Update the last candle with new data
        const time = (new Date(data.timestamp).getTime() / 1000) as Time

        if (data.bar) {
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
        console.error('Error updating live chart data:', error)
      }
    }

    wsService.on('market_update', handleMarketUpdate)

    return () => {
      wsService.off('market_update', handleMarketUpdate)
    }
  }, [symbol])

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
                onChange={() => setSymbol(symbol)} // Force re-render
                className="rounded"
              />
              Show Positions
            </label>
            <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
              <input
                type="checkbox"
                checked={showOrders}
                onChange={() => setSymbol(symbol)} // Force re-render
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
        <div ref={chartContainerRef} className="rounded overflow-hidden" />
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

