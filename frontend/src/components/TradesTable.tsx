import { useMemo, useState } from 'react'
import { useQuery } from 'react-query'
import { tradeApi } from '../services/api'
import { useAccount } from '../contexts/AccountContext'
import type { TradesResponse, Trade } from '../types'
import { Download, ChevronDown, ChevronUp } from 'lucide-react'

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
  const [limit, setLimit] = useState(20)
  const [isOpen, setIsOpen] = useState(true)

  const { data, isLoading } = useQuery<TradesResponse>(
    ['trades', selectedAccount?.id, limit],
    () =>
      tradeApi.getTrades({
        accountId: selectedAccount?.id,
        limit,
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

  const handleExportCSV = () => {
    const accountId = selectedAccount?.id
    const params = new URLSearchParams()
    if (accountId) params.append('account_id', accountId)
    
    const url = `/api/trades/export${params.toString() ? `?${params.toString()}` : ''}`
    window.open(url, '_blank')
  }

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl shadow-sm">
      <button
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        className="w-full flex items-center justify-between px-4 py-3"
      >
        <div className="flex items-center gap-3 text-left">
          <div>
            <p className="text-sm font-semibold text-slate-200">Recent Trades</p>
            <p className="text-xs text-slate-400">
              {trades.length > 0 ? `${trades.length} trades` : 'No trades'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isOpen ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
        </div>
      </button>

      {isOpen && (
        <div className="px-4 pb-4">
          <div className="flex items-center justify-between mb-4">
            <button
              onClick={handleExportCSV}
              className="px-3 py-2 bg-blue-500/20 text-blue-300 rounded hover:bg-blue-500/30 transition-colors flex items-center gap-2 text-sm"
              disabled={!selectedAccount || trades.length === 0}
            >
              <Download className="w-4 h-4" />
              Export CSV
            </button>
            <div className="flex items-center gap-4">
          <label className="text-xs text-slate-400 flex items-center gap-2">
            Rows
            <select
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              className="bg-slate-900 border border-slate-700 text-slate-200 text-xs px-2 py-1 rounded focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              {[20, 50, 100].map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          {summary && (
            <div className="flex flex-col items-end text-xs text-slate-400">
              <div>
                Gross PnL (shown trades):{' '}
                <span className={summary.gross_pnl >= 0 ? 'text-green-400 font-semibold' : 'text-red-400 font-semibold'}>
                  {summary.gross_pnl >= 0 ? '+' : ''}
                  {formatterCurrency.format(Math.abs(summary.gross_pnl))}
                </span>
              </div>
              {summary.total_in_period && summary.displayed_count && summary.total_in_period > summary.displayed_count && (
                <div className="text-slate-500 mt-1">
                  Showing {summary.displayed_count} of {summary.total_in_period} total trades
                </div>
              )}
            </div>
          )}
          </div>
        </div>

        {isLoading ? (
        <div className="py-10 text-center text-slate-400 text-sm">Loading trades...</div>
      ) : trades.length === 0 ? (
        <div className="py-10 text-center text-slate-500 text-sm">No trades in this period.</div>
      ) : (
          <div className="overflow-x-auto">
            <div className="max-h-[48rem] overflow-y-auto pr-1">
            <table className="min-w-full text-sm">
              <thead className="sticky top-0 bg-slate-800">
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
          </div>
        )}
        </div>
      )}
    </div>
  )
}
