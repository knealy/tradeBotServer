import { useQuery, useMutation } from 'react-query'
import { positionApi } from '../services/api'
import { TrendingUp, TrendingDown, ChevronDown, ChevronUp, X } from 'lucide-react'
import { useAccount } from '../contexts/AccountContext'
import { useWidgetState } from '../hooks/useWidgetState'

export default function PositionsOverview() {
  const { selectedAccount } = useAccount()
  const accountId = selectedAccount?.id
  const [isOpen, setIsOpen] = useWidgetState('positionsOverview', true)

  const { data: positions = [], isLoading } = useQuery(
    ['positions', accountId],
    positionApi.getPositions,
    {
      enabled: !!accountId,
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    }
  )

  const closePositionMutation = useMutation(
    ({ positionId, quantity }: { positionId: string; quantity?: number }) =>
      positionApi.closePosition(positionId, quantity),
    {
      onSuccess: () => {
        // Query will auto-refetch
      },
    }
  )

  const handleClosePosition = (positionId?: string, quantity?: number) => {
    if (!positionId) return
    if (confirm(quantity ? `Close ${quantity} contracts?` : 'Close entire position?')) {
      closePositionMutation.mutate({ positionId, quantity })
    }
  }

  if (isLoading) {
    return (
      <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
        <p className="text-slate-400">Loading positions...</p>
      </div>
    )
  }

  if (positions.length === 0) {
    return (
      <div className="bg-slate-800/50 rounded-xl p-6 border border-slate-700/50">
        <p className="text-slate-400">No open positions</p>
      </div>
    )
  }

  return (
    <div className="bg-slate-800/50 border border-slate-700/50 rounded-xl shadow-sm">
      <button
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        className="w-full flex items-center justify-between px-4 py-3"
      >
        <div className="flex items-center gap-3 text-left">
          <div>
            <p className="text-sm font-semibold text-slate-200">Open Positions</p>
            <p className="text-xs text-slate-400">
              {positions.length > 0 ? `${positions.length} position${positions.length !== 1 ? 's' : ''}` : 'No positions'}
            </p>
          </div>
        </div>
        {isOpen ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
      </button>

      {isOpen && (
        <div className="px-4 pb-4">
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
                <div className="flex items-center gap-2">
                  <div className={`flex items-center gap-1 ${pnlColor}`}>
                    <PnlIcon className="w-4 h-4" />
                    <span className="font-semibold">
                      ${unrealized.toFixed(2)}
                    </span>
                  </div>
                  <button
                    onClick={() => handleClosePosition(position.id)}
                    disabled={closePositionMutation.isLoading}
                    className="p-1.5 hover:bg-red-500/20 text-red-400 rounded transition-colors disabled:opacity-50"
                    title="Close position"
                  >
                    <X className="w-4 h-4" />
                  </button>
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
      )}
    </div>
  )
}

