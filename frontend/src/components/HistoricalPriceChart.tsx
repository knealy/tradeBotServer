import { useState, useMemo } from 'react'
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
  Brush
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
const CandlestickShape = (props: any) => {
  const { x, y, width, height, payload } = props
  
  if (!payload) return null
  
  const { open, close, high, low } = payload
  
  if (open === undefined || close === undefined || high === undefined || low === undefined) return null
  
  const isPositive = close >= open
  const color = isPositive ? '#10B981' : '#EF4444' // green : red
  
  // Calculate the scale factor to convert price to pixels
  // The Bar component gives us the y and height for the full high-low range
  const priceRange = high - low
  const pixelsPerPoint = height / priceRange
  
  // Calculate body dimensions
  const bodyTop = isPositive ? close : open
  const bodyHeight = Math.abs(close - open) * pixelsPerPoint
  const bodyY = y + (high - bodyTop) * pixelsPerPoint
  
  // Calculate wick positions
  const wickTop = y // High point
  const wickBottom = y + height // Low point
  const bodyTopY = bodyY
  const bodyBottomY = bodyY + bodyHeight
  
  return (
    <g>
      {/* Upper wick (high to body top) */}
      <line
        x1={x + width / 2}
        y1={wickTop}
        x2={x + width / 2}
        y2={bodyTopY}
        stroke={color}
        strokeWidth={1}
      />
      {/* Body (open-close rectangle) */}
      <rect
        x={x + 1}
        y={bodyY}
        width={Math.max(width - 2, 1)}
        height={Math.max(bodyHeight, 1)}
        fill={color}
        stroke={color}
        fillOpacity={isPositive ? 0.6 : 1}
      />
      {/* Lower wick (body bottom to low) */}
      <line
        x1={x + width / 2}
        y1={bodyBottomY}
        x2={x + width / 2}
        y2={wickBottom}
        stroke={color}
        strokeWidth={1}
      />
    </g>
  )
}

export default function HistoricalPriceChart() {
  const [symbol, setSymbol] = useState('MNQ')
  const [timeframe, setTimeframe] = useState('5m')
  const [chartType, setChartType] = useState<'line' | 'candlestick'>('line')
  const [barLimit, setBarLimit] = useState(300)

  const { data, isLoading } = useQuery<HistoricalDataResponse>(
    ['historicalData', symbol, timeframe, barLimit],
    () =>
      analyticsApi.getHistoricalData({
        symbol,
        timeframe,
        limit: barLimit,
      }),
    {
      enabled: Boolean(symbol),
      staleTime: 60_000,
    }
  )

  const chartData = useMemo(() => {
    if (!data?.bars) return []
    return data.bars.map((bar: HistoricalBar) => ({
      label: formatLabel(bar.timestamp, timeframe),
      timestamp: bar.timestamp,
      open: bar.open,
      close: bar.close,
      high: bar.high,
      low: bar.low,
      range: [bar.low, bar.high], // For candlestick rendering
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
            <YAxis stroke="#9CA3AF" domain={['dataMin - 5', 'dataMax + 5']} />
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
        <ResponsiveContainer width="100%" height={400}>
          <ComposedChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey="label" stroke="#9CA3AF" minTickGap={16} />
            <YAxis stroke="#9CA3AF" domain={['dataMin - 5', 'dataMax + 5']} />
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
            <Brush 
              dataKey="label" 
              height={30} 
              stroke="#3B82F6"
              fill="#1E293B"
            />
            <Bar dataKey="high" fill="#8884d8" shape={<CandlestickShape />} />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}
