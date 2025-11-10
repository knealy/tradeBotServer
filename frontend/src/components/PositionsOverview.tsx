import { useQuery } from 'react-query'
import { positionApi } from '../services/api'
import { TrendingUp, TrendingDown } from 'lucide-react'
import { useAccount } from '../contexts/AccountContext'

export default function PositionsOverview() {
  const { selectedAccount } = useAccount()
  const accountId = selectedAccount?.id

  const { data: positions = [], isLoading } = useQuery(
    ['positions', accountId],
    positionApi.getPositions,
    {
      enabled: !!accountId,
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    }
  )

  if (isLoading) {
    return (
      <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
        <p className="text-slate-400">Loading positions...</p>
      </div>
    )
  }

  if (positions.length === 0) {
    return (
      <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
        <h2 className="text-xl font-semibold mb-4">Open Positions</h2>
        <p className="text-slate-400">No open positions</p>
      </div>
    )
  }

  return (
    <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
      <h2 className="text-xl font-semibold mb-4">Open Positions</h2>
      <div className="space-y-3">
        {positions.map((position) => {
          const isLong = position.side === 'LONG'
          const unrealized = Number(position.unrealized_pnl ?? 0)
          const realized = Number(position.realized_pnl ?? 0)
          const entryPrice = Number(position.entry_price ?? 0)
          const currentPrice = Number(position.current_price ?? entryPrice)
          const PnlIcon = unrealized >= 0 ? TrendingUp : TrendingDown
          const pnlColor = unrealized >= 0 ? 'text-green-400' : 'text-red-400'

          return (
            <div
              key={position.id || `${position.symbol}-${position.entry_price}`}
              className="p-4 bg-slate-700/50 rounded-lg border border-slate-600"
            >
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-3">
                  <div className={`px-2 py-1 rounded text-xs font-medium ${
                    isLong ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                  }`}>
                    {position.side}
                  </div>
                  <span className="font-semibold">{position.symbol}</span>
                  <span className="text-slate-400 text-sm">x{Number(position.quantity ?? 0)}</span>
                </div>
                <div className={`flex items-center gap-1 ${pnlColor}`}>
                  <PnlIcon className="w-4 h-4" />
                  <span className="font-semibold">
                    ${unrealized.toFixed(2)}
                  </span>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-4 text-sm">
                <div>
                  <p className="text-slate-400">Entry</p>
                  <p className="font-semibold">${entryPrice.toFixed(2)}</p>
                </div>
                <div>
                  <p className="text-slate-400">Current</p>
                  <p className="font-semibold">${currentPrice.toFixed(2)}</p>
                </div>
                <div>
                  <p className="text-slate-400">Realized P&L</p>
                  <p className={`font-semibold ${
                    realized >= 0 ? 'text-green-400' : 'text-red-400'
                  }`}>
                    ${realized.toFixed(2)}
                  </p>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

