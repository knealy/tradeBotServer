import { useState, useMemo } from 'react'
import { useQuery } from 'react-query'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { analyticsApi } from '../services/api'
import type { HistoricalBar, HistoricalDataResponse } from '../types'

const TIMEFRAME_OPTIONS = ['1m', '5m', '15m', '1h', '4h', '1d']

const formatLabel = (timestamp: string, timeframe: string) => {
  const date = new Date(timestamp)
  const options: Intl.DateTimeFormatOptions =
    timeframe === '1d'
      ? { month: 'short', day: 'numeric' }
      : { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }
  return new Intl.DateTimeFormat(undefined, options).format(date)
}

export default function HistoricalPriceChart() {
  const [symbol, setSymbol] = useState('MNQ')
  const [timeframe, setTimeframe] = useState('5m')

  const { data, isLoading } = useQuery<HistoricalDataResponse>(
    ['historicalData', symbol, timeframe],
    () =>
      analyticsApi.getHistoricalData({
        symbol,
        timeframe,
        limit: timeframe === '1d' ? 180 : 300,
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
      close: bar.close,
      high: bar.high,
      low: bar.low,
    }))
  }, [data, timeframe])

  return (
    <div className="bg-slate-800 rounded-lg p-6 border border-slate-700 space-y-4">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
        <h2 className="text-xl font-semibold">Price Chart</h2>
        <div className="flex items-center gap-3">
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

      {isLoading ? (
        <div className="h-64 flex items-center justify-center text-slate-400 text-sm">
          Loading historical prices...
        </div>
      ) : chartData.length === 0 ? (
        <div className="h-64 flex items-center justify-center text-slate-500 text-sm">
          No data available for {symbol}.
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey="label" stroke="#9CA3AF" minTickGap={16} />
            <YAxis stroke="#9CA3AF" domain={['dataMin', 'dataMax']} />
            <Tooltip
              contentStyle={{
                backgroundColor: '#1E293B',
                border: '1px solid #334155',
                borderRadius: '8px',
              }}
            />
            <Line type="monotone" dataKey="close" stroke="#3B82F6" strokeWidth={2} dot={false} name="Close" />
            <Line type="monotone" dataKey="high" stroke="#10B981" strokeWidth={1} dot={false} name="High" opacity={0.4} />
            <Line type="monotone" dataKey="low" stroke="#F59E0B" strokeWidth={1} dot={false} name="Low" opacity={0.4} />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}
