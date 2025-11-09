import { useQuery } from 'react-query'
import { positionApi } from '../services/api'
import { TrendingUp, TrendingDown } from 'lucide-react'

export default function PositionsOverview() {
  const { data: positions = [], isLoading } = useQuery(
    'positions',
    positionApi.getPositions,
    {
      refetchInterval: 10000, // Refetch every 10 seconds (was 5)
      staleTime: 5000, // Consider data fresh for 5 seconds
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
          const PnlIcon = position.unrealized_pnl >= 0 ? TrendingUp : TrendingDown
          const pnlColor = position.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'

          return (
            <div
              key={position.id}
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
                  <span className="text-slate-400 text-sm">x{position.quantity}</span>
                </div>
                <div className={`flex items-center gap-1 ${pnlColor}`}>
                  <PnlIcon className="w-4 h-4" />
                  <span className="font-semibold">
                    ${position.unrealized_pnl.toFixed(2)}
                  </span>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-4 text-sm">
                <div>
                  <p className="text-slate-400">Entry</p>
                  <p className="font-semibold">${position.entry_price.toFixed(2)}</p>
                </div>
                <div>
                  <p className="text-slate-400">Current</p>
                  <p className="font-semibold">${position.current_price.toFixed(2)}</p>
                </div>
                <div>
                  <p className="text-slate-400">Realized P&L</p>
                  <p className={`font-semibold ${
                    position.realized_pnl >= 0 ? 'text-green-400' : 'text-red-400'
                  }`}>
                    ${position.realized_pnl.toFixed(2)}
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

