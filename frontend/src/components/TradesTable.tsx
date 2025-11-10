import { useMemo } from 'react'
import { useQuery } from 'react-query'
import { tradeApi } from '../services/api'
import { useAccount } from '../contexts/AccountContext'
import type { TradesResponse, Trade } from '../types'

const formatterCurrency = new Intl.NumberFormat(undefined, {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

const formatterDate = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

export default function TradesTable() {
  const { selectedAccount } = useAccount()

  const { data, isLoading } = useQuery<TradesResponse>(
    ['trades', selectedAccount?.id],
    () =>
      tradeApi.getTrades({
        accountId: selectedAccount?.id,
        limit: 20,
        type: 'filled',
      }),
    {
      enabled: !!selectedAccount,
      staleTime: 30_000,
    }
  )

  const trades = data?.items ?? []

  const summary = useMemo(() => {
    if (!data?.summary) return null
    return data.summary
  }, [data])

  return (
    <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold">Recent Trades</h2>
        {summary && (
          <div className="text-xs text-slate-400">
            Gross PnL:{' '}
            <span className={summary.gross_pnl >= 0 ? 'text-green-400 font-semibold' : 'text-red-400 font-semibold'}>
              {summary.gross_pnl >= 0 ? '+' : ''}
              {formatterCurrency.format(Math.abs(summary.gross_pnl))}
            </span>
          </div>
        )}
      </div>

      {isLoading ? (
        <div className="py-10 text-center text-slate-400 text-sm">Loading trades...</div>
      ) : trades.length === 0 ? (
        <div className="py-10 text-center text-slate-500 text-sm">No trades in this period.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-slate-400 text-xs uppercase tracking-wide">
                <th className="pb-3 text-left">Time</th>
                <th className="pb-3 text-left">Symbol</th>
                <th className="pb-3 text-left">Side</th>
                <th className="pb-3 text-right">Qty</th>
                <th className="pb-3 text-right">Price</th>
                <th className="pb-3 text-right">Net P&L</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700/60">
              {trades.map((trade: Trade) => {
                const netPnlClass = trade.net_pnl >= 0 ? 'text-green-400' : 'text-red-400'
                return (
                  <tr key={trade.id} className="hover:bg-slate-700/40 transition-colors">
                    <td className="py-2 text-slate-300">{formatterDate.format(new Date(trade.timestamp))}</td>
                    <td className="py-2 font-semibold text-slate-100">{trade.symbol}</td>
                    <td className={`py-2 uppercase ${trade.side === 'BUY' ? 'text-green-300' : 'text-red-300'}`}> 
                      {trade.side}
                    </td>
                    <td className="py-2 text-right text-slate-200">{trade.quantity}</td>
                    <td className="py-2 text-right text-slate-200">
                      {trade.price ? formatterCurrency.format(trade.price) : '—'}
                    </td>
                    <td className={`py-2 text-right font-semibold ${netPnlClass}`}>
                      {trade.net_pnl >= 0 ? '+' : ''}
                      {formatterCurrency.format(Math.abs(trade.net_pnl))}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
