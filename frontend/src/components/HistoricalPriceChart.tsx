import { useState, useMemo, useRef } from 'react'
import { useQuery } from 'react-query'
import { 
  LineChart, 
  Line, 
  ComposedChart,
  Bar,
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  ResponsiveContainer,
  Brush,
  ReferenceLine
} from 'recharts'
import { analyticsApi } from '../services/api'
import type { HistoricalBar, HistoricalDataResponse } from '../types'

const TIMEFRAME_OPTIONS = ['1m', '5m', '15m', '1h', '4h', '1d']
const CHART_TYPES = [
  { value: 'line', label: 'Line' },
  { value: 'candlestick', label: 'Candlestick' }
]
const BAR_LIMITS = [
  { value: 100, label: '100' },
  { value: 300, label: '300' },
  { value: 500, label: '500' },
  { value: 1000, label: '1000' }
]

const formatLabel = (timestamp: string, timeframe: string) => {
  const date = new Date(timestamp)
  const options: Intl.DateTimeFormatOptions =
    timeframe === '1d'
      ? { month: 'short', day: 'numeric' }
      : { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }
  return new Intl.DateTimeFormat(undefined, options).format(date)
}

// Custom Candlestick shape component
// This component receives props from Recharts Bar component and renders OHLC candlesticks
const CandlestickShape = (props: any) => {
  const { x, y, width, payload } = props
  
  if (!payload) return null
  
  const { open, close, high, low, _yDomainMin, _yDomainMax, _yDomainRange } = payload
  
  if (open === undefined || close === undefined || high === undefined || low === undefined) return null
  if (!_yDomainMin || !_yDomainMax || !_yDomainRange) return null
  
  const isPositive = close >= open
  // Use green/red color scheme (lighter colors for better visibility on dark background)
  // Green for up candles, red for down candles
  const bodyColor = isPositive ? '#26A69A' : '#EF5350' // Teal green / Red
  const wickColor = isPositive ? '#26A69A' : '#EF5350'
  const bodyStrokeColor = isPositive ? '#1DE9B6' : '#FF5252' // Lighter borders
  
  // Calculate Y positions using the Y-axis domain
  // The y prop from Bar represents the position of the 'high' value according to Recharts' scale
  // In SVG, Y increases downward, so higher prices have lower Y values
  // We need to calculate where low, open, and close should be using the same scale
  
  // Chart height (matches ResponsiveContainer height)
  const chartHeight = 400
  // Calculate pixels per price unit using the domain
  const pixelsPerPrice = chartHeight / _yDomainRange
  
  // Calculate Y positions (inverted: higher price = lower Y)
  // The y prop is the Y position of the high value (already calculated by Recharts)
  const highY = y
  
  // Calculate positions for low, open, and close relative to high
  // Since higher prices = lower Y values, we add the price difference * scale
  const lowY = highY + (high - low) * pixelsPerPrice
  const openY = highY + (high - open) * pixelsPerPrice
  const closeY = highY + (high - close) * pixelsPerPrice
  
  // Body: rectangle between open and close
  const bodyTop = Math.min(openY, closeY)
  const bodyBottom = Math.max(openY, closeY)
  const bodyHeight = Math.abs(closeY - openY)
  
  // Ensure minimum body height for visibility (at least 1px, 2px if open === close)
  const minBodyHeight = open === close ? 2 : 1
  const actualBodyHeight = Math.max(bodyHeight, minBodyHeight)
  
  // Calculate candle width (leave some space between candles)
  const candleWidth = Math.max(width - 2, 2)
  const candleX = x + (width - candleWidth) / 2
  
  return (
    <g>
      {/* Upper wick: vertical line from high to body top */}
      <line
        x1={x + width / 2}
        y1={highY}
        x2={x + width / 2}
        y2={bodyTop}
        stroke={wickColor}
        strokeWidth={1.5}
      />
      {/* Body: rectangle between open and close */}
      <rect
        x={candleX}
        y={bodyTop}
        width={candleWidth}
        height={actualBodyHeight}
        fill={bodyColor}
        stroke={bodyStrokeColor}
        strokeWidth={1}
        fillOpacity={isPositive ? 0.8 : 0.8}
      />
      {/* Lower wick: vertical line from body bottom to low */}
      <line
        x1={x + width / 2}
        y1={bodyBottom}
        x2={x + width / 2}
        y2={lowY}
        stroke={wickColor}
        strokeWidth={1.5}
      />
    </g>
  )
}

export default function HistoricalPriceChart() {
  const [symbol, setSymbol] = useState('MNQ')
  const [timeframe, setTimeframe] = useState('5m')
  const [chartType, setChartType] = useState<'line' | 'candlestick'>('line')
  const [barLimit, setBarLimit] = useState(300)
  const [startDate, setStartDate] = useState<string>('')
  const [endDate, setEndDate] = useState<string>('')
  const [showCrosshair, setShowCrosshair] = useState(false)
  const [crosshairPrice, setCrosshairPrice] = useState<number | null>(null)
  const [crosshairTime, setCrosshairTime] = useState<string | null>(null)
  const chartContainerRef = useRef<HTMLDivElement>(null)

  // Calculate end_time - default to current time if not specified
  const endTime = useMemo(() => {
    if (endDate) {
      return new Date(endDate).toISOString()
    }
    // Default to current time to show most recent data
    return new Date().toISOString()
  }, [endDate])

  const { data, isLoading } = useQuery<HistoricalDataResponse>(
    ['historicalData', symbol, timeframe, barLimit, endTime],
    () =>
      analyticsApi.getHistoricalData({
        symbol,
        timeframe,
        limit: barLimit,
        end: endTime,
      }),
    {
      enabled: Boolean(symbol),
      staleTime: 60_000,
      refetchInterval: chartType === 'candlestick' ? 30_000 : false, // Refresh every 30s for live data
    }
  )

  const chartData = useMemo(() => {
    if (!data?.bars) return []
    const bars = data.bars.map((bar: HistoricalBar) => ({
      label: formatLabel(bar.timestamp, timeframe),
      timestamp: bar.timestamp,
      open: bar.open,
      close: bar.close,
      high: bar.high,
      low: bar.low,
      range: [bar.low, bar.high], // For candlestick rendering
    }))
    
    // Calculate Y-axis domain for candlestick rendering
    const allPrices = bars.flatMap(b => [b.high, b.low, b.open, b.close])
    const minPrice = Math.min(...allPrices)
    const maxPrice = Math.max(...allPrices)
    const yDomainMin = minPrice - 5
    const yDomainMax = maxPrice + 5
    
    // Add domain info to each bar for candlestick calculation
    return bars.map(bar => ({
      ...bar,
      _yDomainMin: yDomainMin,
      _yDomainMax: yDomainMax,
      _yDomainRange: yDomainMax - yDomainMin,
    }))
  }, [data, timeframe])

  return (
    <div className="bg-slate-800 rounded-lg p-6 border border-slate-700 space-y-4">
      <div className="flex flex-col gap-3">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
          <h2 className="text-xl font-semibold">Price Chart</h2>
          <div className="flex items-center gap-3 flex-wrap">
            <input
              value={symbol}
              onChange={(event) => setSymbol(event.target.value.toUpperCase())}
              className="bg-slate-900 border border-slate-700 rounded px-3 py-1 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500"
              placeholder="Symbol"
              maxLength={10}
            />
            <div className="flex gap-1">
              {TIMEFRAME_OPTIONS.map((option) => (
                <button
                  key={option}
                  onClick={() => setTimeframe(option)}
                  className={`px-3 py-1 text-xs font-semibold rounded-full transition-colors ${
                    timeframe === option ? 'bg-primary-500 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                  }`}
                >
                  {option.toUpperCase()}
                </button>
              ))}
            </div>
          </div>
        </div>
        
        {/* Chart Type and Bars controls */}
        <div className="flex items-center gap-3 text-sm flex-wrap">
          <span className="text-slate-400">Chart Type:</span>
          <div className="flex gap-1">
            {CHART_TYPES.map((type) => (
              <button
                key={type.value}
                onClick={() => setChartType(type.value as 'line' | 'candlestick')}
                className={`px-3 py-1 text-xs font-semibold rounded transition-colors ${
                  chartType === type.value ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                }`}
              >
                {type.label}
              </button>
            ))}
          </div>
          
          <span className="text-slate-400 ml-4">Bars:</span>
          <div className="flex gap-1">
            {BAR_LIMITS.map((limit) => (
              <button
                key={limit.value}
                onClick={() => setBarLimit(limit.value)}
                className={`px-3 py-1 text-xs font-semibold rounded transition-colors ${
                  barLimit === limit.value ? 'bg-green-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                }`}
              >
                {limit.label}
              </button>
            ))}
          </div>
        </div>

        {/* Date Range Picker */}
        <div className="flex items-center gap-3 text-sm flex-wrap">
          <span className="text-slate-400">Date Range:</span>
          <input
            type="datetime-local"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="bg-slate-900 border border-slate-700 rounded px-3 py-1 text-xs text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500"
            placeholder="Start Date"
          />
          <span className="text-slate-500">to</span>
          <input
            type="datetime-local"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="bg-slate-900 border border-slate-700 rounded px-3 py-1 text-xs text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500"
            placeholder="End Date (default: now)"
          />
          {(startDate || endDate) && (
            <button
              onClick={() => {
                setStartDate('')
                setEndDate('')
              }}
              className="px-3 py-1 text-xs font-semibold rounded bg-slate-700 text-slate-300 hover:bg-slate-600"
            >
              Clear
            </button>
          )}
        </div>
      </div>

      {isLoading ? (
        <div className="h-96 flex items-center justify-center text-slate-400 text-sm">
          Loading historical prices...
        </div>
      ) : chartData.length === 0 ? (
        <div className="h-96 flex items-center justify-center text-slate-500 text-sm">
          No data available for {symbol}.
        </div>
      ) : chartType === 'line' ? (
        <ResponsiveContainer width="100%" height={400}>
          <LineChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey="label" stroke="#9CA3AF" minTickGap={16} />
            <YAxis 
              stroke="#9CA3AF" 
              domain={['dataMin - 5', 'dataMax + 5']}
              orientation="right"
              width={80}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: '#1E293B',
                border: '1px solid #334155',
                borderRadius: '8px',
              }}
            />
            <Brush 
              dataKey="label" 
              height={30} 
              stroke="#3B82F6"
              fill="#1E293B"
            />
            <Line type="monotone" dataKey="close" stroke="#3B82F6" strokeWidth={2} dot={false} name="Close" />
            <Line type="monotone" dataKey="high" stroke="#10B981" strokeWidth={1} dot={false} name="High" opacity={0.4} />
            <Line type="monotone" dataKey="low" stroke="#F59E0B" strokeWidth={1} dot={false} name="Low" opacity={0.4} />
          </LineChart>
        </ResponsiveContainer>
      ) : (
        <div 
          ref={chartContainerRef}
          className="relative"
          onMouseEnter={() => setShowCrosshair(true)}
          onMouseLeave={() => {
            setShowCrosshair(false)
            setCrosshairPrice(null)
            setCrosshairTime(null)
          }}
          style={{ cursor: showCrosshair ? 'crosshair' : 'default' }}
        >
          <ResponsiveContainer width="100%" height={400}>
            <ComposedChart 
              data={chartData}
              onMouseMove={(e: any) => {
                if (e && e.activePayload && e.activePayload[0]) {
                  const data = e.activePayload[0].payload
                  setCrosshairPrice(data.close)
                  setCrosshairTime(data.label)
                }
              }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
              <XAxis dataKey="label" stroke="#9CA3AF" minTickGap={16} />
              <YAxis 
                stroke="#9CA3AF" 
                domain={['dataMin - 5', 'dataMax + 5']}
                orientation="right"
                width={80}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#1E293B',
                  border: '1px solid #334155',
                  borderRadius: '8px',
                }}
                content={({ active, payload }) => {
                  if (active && payload && payload.length) {
                    const data = payload[0].payload
                    const isPositive = data.close >= data.open
                    return (
                      <div className="bg-slate-900 border border-slate-700 rounded p-3 text-xs">
                        <p className="text-slate-400 mb-1">{data.label}</p>
                        <p className="text-green-400">O: ${data.open?.toFixed(2)}</p>
                        <p className="text-blue-400">H: ${data.high?.toFixed(2)}</p>
                        <p className="text-orange-400">L: ${data.low?.toFixed(2)}</p>
                        <p className={isPositive ? 'text-green-400 font-semibold' : 'text-red-400 font-semibold'}>
                          C: ${data.close?.toFixed(2)}
                        </p>
                        <p className={isPositive ? 'text-green-400' : 'text-red-400'}>
                          {isPositive ? '▲' : '▼'} ${Math.abs(data.close - data.open).toFixed(2)} ({((data.close - data.open) / data.open * 100).toFixed(2)}%)
                        </p>
                      </div>
                    )
                  }
                  return null
                }}
              />
              {/* Crosshair reference lines */}
              {showCrosshair && crosshairPrice !== null && (
                <>
                  <ReferenceLine 
                    y={crosshairPrice} 
                    stroke="#3B82F6" 
                    strokeDasharray="2 2"
                    strokeOpacity={0.5}
                  />
                </>
              )}
              <Brush 
                dataKey="label" 
                height={30} 
                stroke="#3B82F6"
                fill="#1E293B"
                startIndex={Math.max(0, chartData.length - 100)} // Show last 100 bars by default
              />
              <Bar dataKey="high" fill="#8884d8" shape={<CandlestickShape />} />
            </ComposedChart>
          </ResponsiveContainer>
          {/* Crosshair price/time display */}
          {showCrosshair && crosshairPrice !== null && crosshairTime && (
            <div className="absolute top-2 right-2 bg-slate-900 border border-slate-700 rounded px-3 py-2 text-xs pointer-events-none z-10">
              <p className="text-slate-400">{crosshairTime}</p>
              <p className="text-blue-400 font-semibold">${crosshairPrice.toFixed(2)}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
