import { useMemo, useState } from 'react'
import { useQuery } from 'react-query'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { analyticsApi } from '../services/api'
import { useAccount } from '../contexts/AccountContext'
import type { PerformanceHistoryResponse } from '../types'

const INTERVAL_OPTIONS = [
  { label: 'Trade', value: 'trade' },
  { label: 'Day', value: 'day' },
  { label: 'Week', value: 'week' },
  { label: 'Month', value: 'month' },
]

export default function PerformanceChart() {
  const { selectedAccount } = useAccount()
  const [interval, setInterval] = useState<string>('day')

  const { data, isLoading }: { data?: PerformanceHistoryResponse; isLoading: boolean } = useQuery(
    ['performanceHistory', selectedAccount?.id, interval],
    () =>
      analyticsApi.getPerformanceHistory({
        accountId: selectedAccount?.id,
        interval,
      }),
    {
      enabled: !!selectedAccount,
      staleTime: 60_000,
    }
  )

  const chartData = useMemo(() => {
    if (!data?.points) return []
    const startBalance = data.summary?.start_balance ?? 0
    const dateFormatter = new Intl.DateTimeFormat(undefined, {
      month: 'short',
      day: 'numeric',
      hour: interval === 'trade' || interval === 'hour' ? '2-digit' : undefined,
      minute: interval === 'trade' || interval === 'hour' ? '2-digit' : undefined,
    })
    return data.points.map((point) => {
      const label = dateFormatter.format(new Date(point.timestamp))
      return {
        label,
        cumulative: point.cumulative_pnl,
        period: point.period_pnl,
        balance: startBalance + point.cumulative_pnl,
        trades: point.trade_count,
      }
    })
  }, [data, interval])

  const summary = data?.summary

  return (
    <div className="bg-slate-800 rounded-lg p-6 border border-slate-700 space-y-4">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
        <h2 className="text-xl font-semibold">Performance</h2>
        <div className="flex flex-wrap gap-2">
          {INTERVAL_OPTIONS.map((option) => (
            <button
              key={option.value}
              onClick={() => setInterval(option.value)}
              className={`px-3 py-1 rounded-full text-xs font-semibold transition-colors ${
                interval === option.value
                  ? 'bg-primary-500 text-white'
                  : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="h-64 flex items-center justify-center text-slate-400 text-sm">
          Loading performance history...
        </div>
      ) : chartData.length === 0 ? (
        <div className="h-64 flex items-center justify-center text-slate-500 text-sm">
          No historical trades available for this period.
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={320}>
          <LineChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey="label" stroke="#9CA3AF" minTickGap={16} />
            <YAxis stroke="#9CA3AF" yAxisId="left" />
            <YAxis stroke="#9CA3AF" yAxisId="right" orientation="right" />
            <Tooltip
              contentStyle={{
                backgroundColor: '#1E293B',
                border: '1px solid #334155',
                borderRadius: '8px',
              }}
            />
            <Legend />
            <Line
              type="monotone"
              dataKey="cumulative"
              stroke="#10B981"
              strokeWidth={2}
              yAxisId="left"
              name="Cumulative P&L"
              dot={false}
            />
            <Line
              type="monotone"
              dataKey="balance"
              stroke="#3B82F6"
              strokeWidth={2}
              yAxisId="right"
              name="Balance"
              dot={false}
            />
            <Line
              type="monotone"
              dataKey="period"
              stroke="#F59E0B"
              strokeWidth={1}
              strokeDasharray="4 4"
              yAxisId="left"
              name="Period P&L"
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      )}

      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm bg-slate-900/60 rounded-lg p-4 border border-slate-700/60">
          <div>
            <p className="text-slate-400">Total P&L</p>
            <p className={`font-semibold ${summary.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {summary.total_pnl >= 0 ? '+' : ''}${Math.abs(summary.total_pnl).toLocaleString()}
            </p>
          </div>
          <div>
            <p className="text-slate-400">Win Rate</p>
            <p className="font-semibold">{summary.win_rate.toFixed(2)}%</p>
          </div>
          <div>
            <p className="text-slate-400">Max Drawdown</p>
            <p className="font-semibold">-${Math.abs(summary.max_drawdown).toLocaleString()}</p>
          </div>
          <div>
            <p className="text-slate-400">Trades</p>
            <p className="font-semibold">{summary.trade_count}</p>
          </div>
        </div>
      )}
    </div>
  )
}

