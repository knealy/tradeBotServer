import { useQuery, useMutation, useQueryClient } from 'react-query'
import { useAccount } from '../contexts/AccountContext'
import { positionApi, orderApi } from '../services/api'
import AccountSelector from '../components/AccountSelector'
import { TrendingUp, TrendingDown, X, AlertCircle } from 'lucide-react'
import type { Position, Order } from '../types'

export default function PositionsPage() {
  const { accounts, selectedAccount, setSelectedAccount } = useAccount()
  const accountId = selectedAccount?.id
  const queryClient = useQueryClient()

  // Fetch positions
  const { data: positions = [], isLoading: positionsLoading } = useQuery<Position[]>(
    ['positions', accountId],
    positionApi.getPositions,
    {
      enabled: !!accountId,
      staleTime: 30_000,
      refetchInterval: 10_000, // Refresh every 10 seconds
    }
  )

  // Fetch orders
  const { data: orders = [], isLoading: ordersLoading } = useQuery<Order[]>(
    ['orders', accountId],
    orderApi.getOrders,
    {
      enabled: !!accountId,
      staleTime: 30_000,
      refetchInterval: 10_000, // Refresh every 10 seconds
    }
  )

  // Cancel order mutation
  const cancelOrderMutation = useMutation(
    (orderId: string) => orderApi.cancelOrder(orderId),
    {
      onSuccess: () => {
        queryClient.invalidateQueries(['orders', accountId])
      },
    }
  )

  const handleCancelOrder = (orderId: string) => {
    if (confirm('Are you sure you want to cancel this order?')) {
      cancelOrderMutation.mutate(orderId)
    }
  }

  const isLoading = positionsLoading || ordersLoading

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Positions & Orders</h1>
          <p className="text-slate-400 mt-2">Monitor and manage your open positions and orders</p>
        </div>
      </div>

      {/* Account Selection */}
      <div className="max-w-md">
        <AccountSelector
          accounts={accounts}
          selectedAccount={selectedAccount}
          onAccountChange={setSelectedAccount}
        />
      </div>

      {/* Open Positions */}
      <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
        <h2 className="text-xl font-semibold mb-4">Open Positions ({positions.length})</h2>
        {isLoading ? (
          <div className="text-center py-8">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-500 mx-auto"></div>
            <p className="text-slate-400 mt-2">Loading positions...</p>
          </div>
        ) : positions.length === 0 ? (
          <div className="text-center py-8 text-slate-400">
            <AlertCircle className="w-12 h-12 mx-auto mb-2 opacity-50" />
            <p>No open positions</p>
          </div>
        ) : (
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
                  className="p-4 bg-slate-700/50 rounded-lg border border-slate-600 hover:border-slate-500 transition-colors"
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
        )}
      </div>

      {/* Open Orders */}
      <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
        <h2 className="text-xl font-semibold mb-4">Open Orders ({orders.length})</h2>
        {isLoading ? (
          <div className="text-center py-8">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-500 mx-auto"></div>
            <p className="text-slate-400 mt-2">Loading orders...</p>
          </div>
        ) : orders.length === 0 ? (
          <div className="text-center py-8 text-slate-400">
            <AlertCircle className="w-12 h-12 mx-auto mb-2 opacity-50" />
            <p>No open orders</p>
          </div>
        ) : (
          <div className="space-y-3">
            {orders.map((order) => {
              const isBuy = order.side === 'BUY'
              const orderType = order.type || 'UNKNOWN'
              const status = order.status || 'PENDING'

              return (
                <div
                  key={order.id}
                  className="p-4 bg-slate-700/50 rounded-lg border border-slate-600 hover:border-slate-500 transition-colors"
                >
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-3">
                      <div className={`px-2 py-1 rounded text-xs font-medium ${
                        isBuy ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                      }`}>
                        {order.side}
                      </div>
                      <span className="font-semibold">{order.symbol}</span>
                      <span className="text-slate-400 text-sm">x{Number(order.quantity ?? 0)}</span>
                      <span className={`px-2 py-1 rounded text-xs ${
                        status === 'FILLED' ? 'bg-green-500/20 text-green-400' :
                        status === 'CANCELLED' ? 'bg-red-500/20 text-red-400' :
                        'bg-yellow-500/20 text-yellow-400'
                      }`}>
                        {status}
                      </span>
                    </div>
                    {status !== 'FILLED' && status !== 'CANCELLED' && (
                      <button
                        onClick={() => handleCancelOrder(order.id)}
                        className="px-3 py-1 text-xs bg-red-500/20 text-red-400 rounded hover:bg-red-500/30 transition-colors flex items-center gap-1"
                        disabled={cancelOrderMutation.isLoading}
                      >
                        <X className="w-3 h-3" />
                        Cancel
                      </button>
                    )}
                  </div>
                  <div className="grid grid-cols-3 gap-4 text-sm">
                    <div>
                      <p className="text-slate-400">Type</p>
                      <p className="font-semibold">{orderType}</p>
                    </div>
                    <div>
                      <p className="text-slate-400">Price</p>
                      <p className="font-semibold">
                        {order.price ? `$${Number(order.price).toFixed(2)}` : 'Market'}
                      </p>
                    </div>
                    <div>
                      <p className="text-slate-400">Order ID</p>
                      <p className="font-mono text-xs text-slate-400 truncate">{order.id}</p>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

