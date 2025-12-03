import { useQuery, useMutation, useQueryClient } from 'react-query'
import { useAccount } from '../contexts/AccountContext'
import { positionApi, orderApi, automationApi } from '../services/api'
import { useMarketSocket } from '../hooks/useMarketSocket'
import OrderTicket from '../components/OrderTicket'
import TradingChart from '../components/TradingChart'
import { TrendingUp, TrendingDown, X, AlertCircle, Edit, Trash2, ChevronDown, ChevronUp, Info, Zap, Target, Play, ShoppingCart } from 'lucide-react'
import { useState } from 'react'
import type { Position, Order } from '../types'

type TabType = 'order' | 'automation' | 'positions' | 'orders'

export default function PositionsPage() {
  const { selectedAccount } = useAccount()
  const accountId = selectedAccount?.id
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<TabType>('order')
  const [isTabbedOpen, setIsTabbedOpen] = useState(true)
  const [positionsErrorCount, setPositionsErrorCount] = useState(0)
  const [ordersErrorCount, setOrdersErrorCount] = useState(0)
  
  // Enable live market updates for positions/orders
  useMarketSocket()

  // Fetch positions with more frequent updates
  const { data: positions = [], isLoading: positionsLoading, isError: positionsError } = useQuery<Position[]>(
    ['positions', accountId],
    positionApi.getPositions,
    {
      enabled: !!accountId,
      staleTime: 10_000, // 10 seconds stale time
      // Pause refetching if we've had multiple consecutive errors
      refetchInterval: positionsErrorCount > 2 ? false : 10_000, // Refresh every 10 seconds when healthy
      retry: 1, // Only retry once on failure
      retryDelay: 5000, // Wait 5 seconds before retry
      refetchOnMount: true,
      refetchOnWindowFocus: false,
      onError: () => {
        setPositionsErrorCount((prev) => prev + 1)
      },
      onSuccess: () => {
        setPositionsErrorCount(0) // Reset error count on success
      },
    }
  )

  // Fetch orders
  const { data: orders = [], isLoading: ordersLoading, isError: ordersError } = useQuery<Order[]>(
    ['orders', accountId],
    orderApi.getOrders,
    {
      enabled: !!accountId,
      staleTime: 30_000,
      // Pause refetching if we've had multiple consecutive errors
      refetchInterval: ordersErrorCount > 2 ? false : 10_000, // Refresh every 10 seconds when healthy
      retry: 1, // Only retry once on failure
      retryDelay: 5000, // Wait 5 seconds before retry
      refetchOnMount: true,
      refetchOnWindowFocus: false,
      onError: () => {
        setOrdersErrorCount((prev) => prev + 1)
      },
      onSuccess: () => {
        setOrdersErrorCount(0) // Reset error count on success
      },
    }
  )

  // Modify order state
  const [editingOrder, setEditingOrder] = useState<{ id: string; price?: number; quantity?: number } | null>(null)
  const [modifyPrice, setModifyPrice] = useState('')
  const [modifyQuantity, setModifyQuantity] = useState('')
  const [partialCloseQty, setPartialCloseQty] = useState<Record<string, string>>({})
  const [stopInputs, setStopInputs] = useState<Record<string, string>>({})
  const [takeProfitInputs, setTakeProfitInputs] = useState<Record<string, string>>({})
  const [expandedPosition, setExpandedPosition] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null)
  const [trailingStopInputs, setTrailingStopInputs] = useState<Record<string, string>>({})
  const [trailingStopEnabled, setTrailingStopEnabled] = useState<Record<string, boolean>>({})
  const [breakevenEnabled, setBreakevenEnabled] = useState<Record<string, boolean>>({})

  const pushFeedback = (type: 'success' | 'error', message: string) => {
    setFeedback({ type, message })
    window.setTimeout(() => setFeedback(null), 4000)
  }

  // Cancel order mutation
  const cancelOrderMutation = useMutation(
    (orderId: string) => orderApi.cancelOrder(orderId),
    {
      onSuccess: (_, orderId) => {
        queryClient.invalidateQueries(['orders', accountId])
        if (orderId) {
          pushFeedback('success', `Cancel request sent for order ${orderId}`)
        } else {
          pushFeedback('success', 'Cancel request sent')
        }
      },
      onError: (error: any) => {
        const message = error?.response?.data?.error || error?.message || 'Failed to cancel order'
        pushFeedback('error', message)
      },
    }
  )

  // Modify order mutation
  const modifyOrderMutation = useMutation(
    ({ orderId, updates }: { orderId: string; updates: { price?: number; quantity?: number; order_type?: number } }) =>
      orderApi.modifyOrder(orderId, updates),
    {
      onSuccess: (_, variables) => {
        queryClient.invalidateQueries(['orders', accountId])
        setEditingOrder(null)
        setModifyPrice('')
        setModifyQuantity('')
        pushFeedback('success', `Order ${variables.orderId} updated`)
      },
      onError: (error: any) => {
        const message = error?.response?.data?.error || error?.message || 'Failed to modify order'
        pushFeedback('error', message)
      },
    }
  )

  // Close position mutation
  const closePositionMutation = useMutation(
    ({ positionId, quantity }: { positionId: string; quantity?: number }) =>
      positionApi.closePosition(positionId, quantity),
    {
      onSuccess: (_, variables) => {
        queryClient.invalidateQueries(['positions', accountId])
        if (variables.quantity) {
          setPartialCloseQty((prev) => ({ ...prev, [variables.positionId]: '' }))
          pushFeedback('success', `Partial close submitted for ${variables.quantity} contracts`)
        } else {
          pushFeedback('success', 'Close position request sent')
        }
      },
      onError: (error: any) => {
        const message = error?.response?.data?.error || error?.message || 'Failed to close position'
        pushFeedback('error', message)
      },
    }
  )

  const modifyStopLossMutation = useMutation(
    ({ positionId, stopPrice }: { positionId: string; stopPrice: number }) =>
      positionApi.modifyStopLoss(positionId, stopPrice),
    {
      onSuccess: (_, variables) => {
        queryClient.invalidateQueries(['positions', accountId])
        setStopInputs((prev) => ({ ...prev, [variables.positionId]: '' }))
        pushFeedback('success', 'Stop loss updated')
      },
      onError: (error: any) => {
        const message = error?.response?.data?.error || error?.message || 'Failed to update stop loss'
        pushFeedback('error', message)
      },
    }
  )

  const modifyTakeProfitMutation = useMutation(
    ({ positionId, takeProfit }: { positionId: string; takeProfit: number }) =>
      positionApi.modifyTakeProfit(positionId, takeProfit),
    {
      onSuccess: (_, variables) => {
        queryClient.invalidateQueries(['positions', accountId])
        setTakeProfitInputs((prev) => ({ ...prev, [variables.positionId]: '' }))
        pushFeedback('success', 'Take profit updated')
      },
      onError: (error: any) => {
        const message = error?.response?.data?.error || error?.message || 'Failed to update take profit'
        pushFeedback('error', message)
      },
    }
  )

  // Trailing stop mutations
  const enableTrailingStopMutation = useMutation(
    ({ positionId, trailAmount }: { positionId: string; trailAmount: number }) =>
      positionApi.enableTrailingStop(positionId, trailAmount),
    {
      onSuccess: (_, variables) => {
        queryClient.invalidateQueries(['positions', accountId])
        queryClient.invalidateQueries(['orders', accountId])
        setTrailingStopInputs((prev) => ({ ...prev, [variables.positionId]: '' }))
        setTrailingStopEnabled((prev) => ({ ...prev, [variables.positionId]: true }))
        pushFeedback('success', 'Trailing stop enabled')
      },
      onError: (error: any) => {
        const message = error?.response?.data?.error || error?.message || 'Failed to enable trailing stop'
        pushFeedback('error', message)
      },
    }
  )

  const disableTrailingStopMutation = useMutation(
    (positionId: string) => positionApi.disableTrailingStop(positionId),
    {
      onSuccess: (_, positionId) => {
        queryClient.invalidateQueries(['positions', accountId])
        queryClient.invalidateQueries(['orders', accountId])
        setTrailingStopEnabled((prev) => ({ ...prev, [positionId]: false }))
        pushFeedback('success', 'Trailing stop disabled')
      },
      onError: (error: any) => {
        const message = error?.response?.data?.error || error?.message || 'Failed to disable trailing stop'
        pushFeedback('error', message)
      },
    }
  )

  // Breakeven mutations
  const enableBreakevenMutation = useMutation(
    (positionId: string) => positionApi.enableBreakeven(positionId),
    {
      onSuccess: (_, positionId) => {
        queryClient.invalidateQueries(['positions', accountId])
        setBreakevenEnabled((prev) => ({ ...prev, [positionId]: true }))
        pushFeedback('success', 'Breakeven enabled - stop moved to entry price')
      },
      onError: (error: any) => {
        const message = error?.response?.data?.error || error?.message || 'Failed to enable breakeven'
        pushFeedback('error', message)
      },
    }
  )

  const disableBreakevenMutation = useMutation(
    (positionId: string) => positionApi.disableBreakeven(positionId),
    {
      onSuccess: (_, positionId) => {
        queryClient.invalidateQueries(['positions', accountId])
        setBreakevenEnabled((prev) => ({ ...prev, [positionId]: false }))
        pushFeedback('success', 'Breakeven disabled')
      },
      onError: (error: any) => {
        const message = error?.response?.data?.error || error?.message || 'Failed to disable breakeven'
        pushFeedback('error', message)
      },
    }
  )

  // Test overnight breakout mutation
  const testBreakoutMutation = useMutation(
    ({ symbol, quantity, accountName }: { symbol?: string; quantity?: number; accountName?: string }) =>
      automationApi.testOvernightBreakout(symbol, quantity, accountName),
    {
      onSuccess: () => {
        queryClient.invalidateQueries(['positions', accountId])
        queryClient.invalidateQueries(['orders', accountId])
        pushFeedback('success', 'Overnight breakout test executed')
      },
      onError: (error: any) => {
        const message = error?.response?.data?.error || error?.message || 'Failed to execute test'
        pushFeedback('error', message)
      },
    }
  )

  const handleClosePosition = (positionId?: string, quantity?: number) => {
    if (!positionId) {
      pushFeedback('error', 'Unable to close position: missing identifier')
      return
    }
    const message = quantity 
      ? `Are you sure you want to close ${quantity} contracts of this position?`
      : 'Are you sure you want to close this entire position?'
    if (confirm(message)) {
      closePositionMutation.mutate({ positionId, quantity })
    }
  }

  const handleCancelOrder = (orderId: string) => {
    if (confirm('Are you sure you want to cancel this order?')) {
      cancelOrderMutation.mutate(orderId)
    }
  }

  const handleStartEdit = (order: Order) => {
    setEditingOrder({ id: order.id, price: order.price, quantity: order.quantity })
    setModifyPrice(order.price ? order.price.toString() : '')
    setModifyQuantity(order.quantity ? order.quantity.toString() : '')
  }

  const handleSaveModify = () => {
    if (!editingOrder) return
    
    const updates: { price?: number; quantity?: number } = {}
    if (modifyPrice && modifyPrice !== editingOrder.price?.toString()) {
      updates.price = parseFloat(modifyPrice)
    }
    if (modifyQuantity && modifyQuantity !== editingOrder.quantity?.toString()) {
      updates.quantity = parseInt(modifyQuantity)
    }
    
    if (Object.keys(updates).length === 0) {
      setEditingOrder(null)
      return
    }
    
    modifyOrderMutation.mutate({ orderId: editingOrder.id, updates })
  }

  const handlePartialClose = (position: Position) => {
    if (!position.id) {
      pushFeedback('error', 'Position ID missing')
      return
    }
    const rawValue = partialCloseQty[position.id] || ''
    const qty = parseInt(rawValue, 10)
    if (!Number.isFinite(qty) || qty <= 0) {
      pushFeedback('error', 'Enter a valid quantity for partial close')
      return
    }
    if (qty > position.quantity) {
      pushFeedback('error', 'Quantity exceeds current position size')
      return
    }
    closePositionMutation.mutate({ positionId: position.id, quantity: qty })
  }

  const handleStopLossUpdate = (position: Position) => {
    if (!position.id) {
      pushFeedback('error', 'Position ID missing')
      return
    }
    const rawValue = stopInputs[position.id] || ''
    const price = parseFloat(rawValue)
    if (!Number.isFinite(price)) {
      pushFeedback('error', 'Enter a valid stop loss price')
      return
    }
    modifyStopLossMutation.mutate({ positionId: position.id, stopPrice: price })
  }

  const handleTakeProfitUpdate = (position: Position) => {
    if (!position.id) {
      pushFeedback('error', 'Position ID missing')
      return
    }
    const rawValue = takeProfitInputs[position.id] || ''
    const price = parseFloat(rawValue)
    if (!Number.isFinite(price)) {
      pushFeedback('error', 'Enter a valid take profit price')
      return
    }
    modifyTakeProfitMutation.mutate({ positionId: position.id, takeProfit: price })
  }

  const toggleExpandedPosition = (positionId?: string | null) => {
    if (!positionId) {
      pushFeedback('error', 'Raw details unavailable for this position')
      return
    }
    setExpandedPosition((prev) => (prev === positionId ? null : positionId))
  }

  const handleTrailingStop = (position: Position) => {
    if (!position.id) {
      pushFeedback('error', 'Position ID missing')
      return
    }
    const isEnabled = trailingStopEnabled[position.id]
    if (isEnabled) {
      disableTrailingStopMutation.mutate(position.id)
    } else {
      const rawValue = trailingStopInputs[position.id] || ''
      const trailAmount = parseFloat(rawValue)
      if (!Number.isFinite(trailAmount) || trailAmount <= 0) {
        pushFeedback('error', 'Enter a valid trail amount (e.g., 25.00)')
        return
      }
      enableTrailingStopMutation.mutate({ positionId: position.id, trailAmount })
    }
  }

  const handleBreakevenToggle = (position: Position) => {
    if (!position.id) {
      pushFeedback('error', 'Position ID missing')
      return
    }
    const isEnabled = breakevenEnabled[position.id]
    if (isEnabled) {
      disableBreakevenMutation.mutate(position.id)
    } else {
      enableBreakevenMutation.mutate(position.id)
    }
  }

  const isLoading = positionsLoading || ordersLoading
  const hasError = positionsError || ordersError

  return (
    <div className="space-y-5 overflow-x-hidden max-w-full">
      {/* Network Error Warning */}
      {hasError && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm text-red-300">
          <p>⚠️ Network error: Unable to fetch positions/orders. Retrying...</p>
          <p className="text-xs text-red-400/80 mt-1">WebSocket updates will continue if connected.</p>
        </div>
      )}

      {feedback && (
        <div
          className={`rounded-lg border px-4 py-2 text-sm ${
            feedback.type === 'success'
              ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
              : 'border-red-500/40 bg-red-500/10 text-red-300'
          }`}
        >
          {feedback.message}
        </div>
      )}

      {/* Price Chart Container */}
      <div className="bg-slate-800 rounded-lg p-3 sm:p-6 border border-slate-700 space-y-4 overflow-x-hidden">
        <TradingChart 
          symbol="MNQ"
          positions={positions}
          orders={orders}
          height={500}
          showPositions={true}
          showOrders={true}
        />
        
        {/* Trading Actions - Right below chart info bar */}
        <div className="bg-slate-800/50 border border-slate-700/50 rounded-xl shadow-sm overflow-x-hidden">
        {/* Collapsible Header */}
        <button
          type="button"
          onClick={() => setIsTabbedOpen((prev) => !prev)}
          className="w-full flex items-center justify-between px-3 sm:px-4 py-2 sm:py-3"
        >
          <div className="flex items-center gap-2 sm:gap-3 text-left min-w-0">
            <div className="min-w-0">
              <p className="text-xs sm:text-sm font-semibold text-slate-200 truncate">Trading Actions</p>
              <p className="text-xs text-slate-400 hidden sm:block">Order Ticket, Positions, Orders, Automation</p>
            </div>
          </div>
          {isTabbedOpen ? <ChevronUp className="w-4 h-4 text-slate-400 shrink-0" /> : <ChevronDown className="w-4 h-4 text-slate-400 shrink-0" />}
        </button>

        {isTabbedOpen && (
          <>
            {/* Tabs */}
            <div className="flex items-center gap-1 border-b border-slate-700/50 px-2 sm:px-4 pt-2 overflow-x-auto">
          <button
            type="button"
            onClick={() => setActiveTab('order')}
            className={`flex items-center gap-1 sm:gap-2 px-2 sm:px-4 py-2 sm:py-2.5 rounded-t-lg text-xs sm:text-sm font-medium transition-all whitespace-nowrap shrink-0 ${
              activeTab === 'order'
                ? 'bg-slate-800 text-primary-400 border-t border-x border-slate-700 -mb-px'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <ShoppingCart className="w-3 h-3 sm:w-4 sm:h-4" />
            <span className="hidden xs:inline">Order Ticket</span>
            <span className="xs:hidden">Order</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('positions')}
            className={`flex items-center gap-1 sm:gap-2 px-2 sm:px-4 py-2 sm:py-2.5 rounded-t-lg text-xs sm:text-sm font-medium transition-all whitespace-nowrap shrink-0 ${
              activeTab === 'positions'
                ? 'bg-slate-800 text-primary-400 border-t border-x border-slate-700 -mb-px'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <TrendingUp className="w-3 h-3 sm:w-4 sm:h-4" />
            <span className="hidden sm:inline">Positions ({positions.length})</span>
            <span className="sm:hidden">Pos ({positions.length})</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('orders')}
            className={`flex items-center gap-1 sm:gap-2 px-2 sm:px-4 py-2 sm:py-2.5 rounded-t-lg text-xs sm:text-sm font-medium transition-all whitespace-nowrap shrink-0 ${
              activeTab === 'orders'
                ? 'bg-slate-800 text-primary-400 border-t border-x border-slate-700 -mb-px'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <Edit className="w-3 h-3 sm:w-4 sm:h-4" />
            <span className="hidden sm:inline">Orders ({orders.length})</span>
            <span className="sm:hidden">Ord ({orders.length})</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('automation')}
            className={`flex items-center gap-1 sm:gap-2 px-2 sm:px-4 py-2 sm:py-2.5 rounded-t-lg text-xs sm:text-sm font-medium transition-all whitespace-nowrap shrink-0 ${
              activeTab === 'automation'
                ? 'bg-slate-800 text-primary-400 border-t border-x border-slate-700 -mb-px'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <Zap className="w-3 h-3 sm:w-4 sm:h-4" />
            <span>Automation</span>
          </button>
        </div>

            {/* Tab Content */}
            <div className="p-3 sm:p-4 overflow-x-hidden">
          {/* Order Ticket Tab */}
          {activeTab === 'order' && <OrderTicket noWrapper={true} />}

          {/* Automation Tools Tab */}
          {activeTab === 'automation' && (
            <div>
              <div className="flex items-center gap-3 mb-4">
                <Zap className="w-5 h-5 text-yellow-400" />
                <div>
                  <p className="text-sm font-semibold text-slate-200">Automation Tools</p>
                  <p className="text-xs text-slate-400">Overnight breakout testing</p>
                </div>
              </div>
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  <button
                    onClick={() => testBreakoutMutation.mutate({ symbol: 'MNQ', quantity: 1, accountName: 'PRAC' })}
                    className="px-4 py-2 bg-blue-500/20 text-blue-300 rounded hover:bg-blue-500/30 transition-colors flex items-center gap-2"
                    disabled={testBreakoutMutation.isLoading || !accountId}
                  >
                    <Play className="w-4 h-4" />
                    {testBreakoutMutation.isLoading ? 'Testing...' : 'Test Overnight Breakout'}
                  </button>
                  <p className="text-sm text-slate-400">
                    Simulates overnight breakout trades on practice account (MNQ, 1 contract)
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Open Positions Tab */}
          {activeTab === 'positions' && (
            <div>
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
                    const stateKey = position.id ?? ''
                    const partialValue = stateKey ? partialCloseQty[stateKey] ?? '' : ''
                    const stopValue = stateKey ? stopInputs[stateKey] ?? '' : ''
                    const takeValue = stateKey ? takeProfitInputs[stateKey] ?? '' : ''
                    const trailingValue = stateKey ? trailingStopInputs[stateKey] ?? '' : ''
                    const isTrailingEnabled = stateKey ? trailingStopEnabled[stateKey] ?? false : false
                    const isBreakevenEnabled = stateKey ? breakevenEnabled[stateKey] ?? false : false
                    const openedLabel = position.timestamp ? new Date(position.timestamp).toLocaleString() : '—'
                    const unrealizedPct =
                      typeof position.unrealized_pnl_pct === 'number' && isFinite(position.unrealized_pnl_pct)
                        ? `${position.unrealized_pnl_pct.toFixed(2)}%`
                        : '—'
                    const tickSize =
                      typeof position.tick_size === 'number' && isFinite(position.tick_size)
                        ? position.tick_size
                        : null
                    const pointValue =
                      typeof position.point_value === 'number' && isFinite(position.point_value)
                        ? position.point_value
                        : null
                    const canControl = Boolean(position.id)
                    const isExpanded = expandedPosition === position.id

                    return (
                      <div
                        key={position.id || `${position.symbol}-${position.entry_price}`}
                        className="p-3 sm:p-4 bg-slate-700/50 rounded-lg border border-slate-600 hover:border-slate-500 transition-colors overflow-x-hidden"
                      >
                        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 sm:gap-3 mb-2">
                          <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
                            <div className={`px-2 py-1 rounded text-xs font-medium shrink-0 ${
                              isLong ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                            }`}>
                              {position.side}
                            </div>
                            <span className="font-semibold text-sm sm:text-base">{position.symbol}</span>
                            <span className="text-slate-400 text-xs sm:text-sm">x{Number(position.quantity ?? 0)}</span>
                          </div>
                          <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
                            <div className={`flex items-center gap-1 ${pnlColor}`}>
                              <PnlIcon className="w-3 h-3 sm:w-4 sm:h-4" />
                              <span className="font-semibold text-sm sm:text-base">
                                ${unrealized.toFixed(2)}
                              </span>
                            </div>
                            <button
                              onClick={() => handleClosePosition(position.id)}
                              className="px-2 sm:px-3 py-1 text-xs bg-red-500/20 text-red-400 rounded hover:bg-red-500/30 transition-colors flex items-center gap-1 shrink-0"
                              disabled={!canControl || closePositionMutation.isLoading}
                            >
                              <Trash2 className="w-3 h-3" />
                              <span className="whitespace-nowrap">Close</span>
                            </button>
                          </div>
                        </div>
                        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6 gap-2 sm:gap-3 md:gap-4 text-xs sm:text-sm">
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
                          <div>
                            <p className="text-slate-400">Unrealized %</p>
                            <p className="font-semibold text-slate-200">{unrealizedPct}</p>
                          </div>
                          <div>
                            <p className="text-slate-400">Stop Loss</p>
                            <p className="font-semibold text-slate-200">
                              {position.stop_loss !== undefined && position.stop_loss !== null
                                ? `$${Number(position.stop_loss).toFixed(2)}`
                                : '—'}
                            </p>
                          </div>
                          <div>
                            <p className="text-slate-400">Take Profit</p>
                            <p className="font-semibold text-slate-200">
                              {position.take_profit !== undefined && position.take_profit !== null
                                ? `$${Number(position.take_profit).toFixed(2)}`
                                : '—'}
                            </p>
                          </div>
                          <div>
                            <p className="text-slate-400">Opened</p>
                            <p className="font-semibold text-slate-200">{openedLabel}</p>
                          </div>
                          <div>
                            <p className="text-slate-400">Tick Size</p>
                            <p className="font-semibold text-slate-200">
                              {tickSize ? tickSize.toFixed(4) : '—'}
                            </p>
                          </div>
                          <div>
                            <p className="text-slate-400">Point Value</p>
                            <p className="font-semibold text-slate-200">
                              {pointValue ? `$${pointValue.toFixed(2)}` : '—'}
                            </p>
                          </div>
                        </div>
                        <div className="mt-3 grid gap-3 sm:grid-cols-2">
                          <div>
                            <p className="text-slate-400 text-xs uppercase tracking-wide">Adjust Stop Loss</p>
                            <div className="mt-1 flex flex-wrap items-center gap-2">
                              <input
                                type="number"
                                value={stopValue}
                                onChange={(e) => {
                                  if (position.id) {
                                    setStopInputs((prev) => ({ ...prev, [position.id as string]: e.target.value }))
                                  }
                                }}
                                placeholder="New stop price"
                                className="w-24 sm:w-28 px-2 py-1 text-xs bg-slate-900 border border-slate-600 rounded text-slate-200"
                                disabled={!canControl}
                              />
                              <button
                                onClick={() => handleStopLossUpdate(position)}
                                className="px-2 sm:px-3 py-1 text-xs bg-blue-500/20 text-blue-300 rounded hover:bg-blue-500/30 transition-colors whitespace-nowrap shrink-0"
                                disabled={!canControl || modifyStopLossMutation.isLoading}
                              >
                                Update SL
                              </button>
                            </div>
                          </div>
                          <div>
                            <p className="text-slate-400 text-xs uppercase tracking-wide">Adjust Take Profit</p>
                            <div className="mt-1 flex flex-wrap items-center gap-2">
                              <input
                                type="number"
                                value={takeValue}
                                onChange={(e) => {
                                  if (position.id) {
                                    setTakeProfitInputs((prev) => ({ ...prev, [position.id as string]: e.target.value }))
                                  }
                                }}
                                placeholder="New target price"
                                className="w-24 sm:w-28 px-2 py-1 text-xs bg-slate-900 border border-slate-600 rounded text-slate-200"
                                disabled={!canControl}
                              />
                              <button
                                onClick={() => handleTakeProfitUpdate(position)}
                                className="px-2 sm:px-3 py-1 text-xs bg-green-500/20 text-green-300 rounded hover:bg-green-500/30 transition-colors whitespace-nowrap shrink-0"
                                disabled={!canControl || modifyTakeProfitMutation.isLoading}
                              >
                                Update TP
                              </button>
                            </div>
                          </div>
                        </div>
                        <div className="mt-3 flex flex-wrap items-center gap-2 sm:gap-3">
                          <div className="flex items-center gap-2">
                            <input
                              type="number"
                              min={1}
                              value={partialValue}
                              onChange={(e) => {
                                if (position.id) {
                                  setPartialCloseQty((prev) => ({ ...prev, [position.id as string]: e.target.value }))
                                }
                              }}
                              placeholder="Qty"
                              className="w-20 px-2 py-1 text-xs bg-slate-900 border border-slate-600 rounded text-slate-200"
                              disabled={!canControl}
                            />
                            <button
                              onClick={() => handlePartialClose(position)}
                              className="px-3 py-1 text-xs bg-amber-500/20 text-amber-300 rounded hover:bg-amber-500/30 transition-colors"
                              disabled={!canControl || closePositionMutation.isLoading}
                            >
                              Partial Close
                            </button>
                          </div>
                          <div className="flex items-center gap-2">
                            <input
                              type="number"
                              step="0.01"
                              value={trailingValue}
                              onChange={(e) => {
                                if (position.id) {
                                  setTrailingStopInputs((prev) => ({ ...prev, [position.id as string]: e.target.value }))
                                }
                              }}
                              placeholder="Trail $"
                              className="w-24 px-2 py-1 text-xs bg-slate-900 border border-slate-600 rounded text-slate-200"
                              disabled={!canControl || isTrailingEnabled}
                            />
                            <button
                              onClick={() => handleTrailingStop(position)}
                              className={`px-3 py-1 text-xs rounded transition-colors flex items-center gap-1 ${
                                isTrailingEnabled
                                  ? 'bg-red-500/20 text-red-300 hover:bg-red-500/30'
                                  : 'bg-purple-500/20 text-purple-300 hover:bg-purple-500/30'
                              }`}
                              disabled={!canControl || enableTrailingStopMutation.isLoading || disableTrailingStopMutation.isLoading}
                            >
                              <Zap className="w-3 h-3" />
                              {isTrailingEnabled ? 'Disable Trail' : 'Enable Trail'}
                            </button>
                          </div>
                          <button
                            onClick={() => handleBreakevenToggle(position)}
                            className={`px-3 py-1 text-xs rounded transition-colors flex items-center gap-1 ${
                              isBreakevenEnabled
                                ? 'bg-green-500/20 text-green-300 hover:bg-green-500/30'
                                : 'bg-slate-600/40 text-slate-200 hover:bg-slate-600/60'
                            }`}
                            disabled={!canControl || enableBreakevenMutation.isLoading || disableBreakevenMutation.isLoading}
                          >
                            <Target className="w-3 h-3" />
                            {isBreakevenEnabled ? 'Breakeven ON' : 'Set Breakeven'}
                          </button>
                          <button
                            onClick={() => toggleExpandedPosition(position.id)}
                            className="px-3 py-1 text-xs bg-slate-600/40 text-slate-200 rounded hover:bg-slate-600/60 transition-colors flex items-center gap-1"
                          >
                            {isExpanded ? (
                              <>
                                <ChevronUp className="w-3 h-3" /> Hide Details
                              </>
                            ) : (
                              <>
                                <ChevronDown className="w-3 h-3" /> View Details
                              </>
                            )}
                          </button>
                        </div>
                        {isExpanded && (
                          <div className="mt-3 bg-slate-900/60 border border-slate-700/70 rounded-lg p-3 text-xs text-left overflow-x-auto">
                            <div className="flex items-center gap-2 text-slate-400 mb-2">
                              <Info className="w-4 h-4" />
                              <span>Raw position payload</span>
                            </div>
                            <pre className="whitespace-pre-wrap text-slate-200">
                              {JSON.stringify(position._raw ?? position, null, 2)}
                            </pre>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {/* Open Orders Tab */}
          {activeTab === 'orders' && (
            <div>
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
                    const status = (order.status || 'PENDING').toUpperCase()
                    const canModify = status !== 'FILLED' && status !== 'CANCELLED' && status !== 'REJECTED'
                    const stopPrice = order.stop_price ? Number(order.stop_price) : null
                    const stopLoss = order.stop_loss ? Number(order.stop_loss) : null
                    const takeProfit = order.take_profit ? Number(order.take_profit) : null
                    const bracketSummary = stopLoss || takeProfit
                      ? `${stopLoss ? `SL $${stopLoss.toFixed(2)}` : ''}${stopLoss && takeProfit ? ' / ' : ''}${takeProfit ? `TP $${takeProfit.toFixed(2)}` : ''}`
                      : '—'

                            return (
                              <div
                                key={order.id}
                                className="p-3 sm:p-4 bg-slate-700/50 rounded-lg border border-slate-600 hover:border-slate-500 transition-colors overflow-x-hidden"
                              >
                                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 sm:gap-3 mb-2">
                                  <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
                                    <div className={`px-2 py-1 rounded text-xs font-medium shrink-0 ${
                                      isBuy ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                                    }`}>
                                      {order.side}
                                    </div>
                                    <span className="font-semibold text-sm sm:text-base">{order.symbol}</span>
                                    <span className="text-slate-400 text-xs sm:text-sm">x{Number(order.quantity ?? 0)}</span>
                                    <span className={`px-2 py-1 rounded text-xs shrink-0 ${
                                      status === 'FILLED' ? 'bg-green-500/20 text-green-400' :
                                      status === 'CANCELLED' ? 'bg-red-500/20 text-red-400' :
                                      'bg-yellow-500/20 text-yellow-400'
                                    }`}>
                                      {status}
                                    </span>
                                  </div>
                                  {canModify && (
                                    <div className="flex items-center gap-2 flex-wrap">
                              {editingOrder?.id === order.id ? (
                                <>
                                  <input
                                    type="number"
                                    value={modifyPrice}
                                    onChange={(e) => setModifyPrice(e.target.value)}
                                    placeholder="Price"
                                    className="w-18 sm:w-20 px-2 py-1 text-xs bg-slate-900 border border-slate-600 rounded text-slate-200"
                                  />
                                  <input
                                    type="number"
                                    value={modifyQuantity}
                                    onChange={(e) => setModifyQuantity(e.target.value)}
                                    placeholder="Qty"
                                    className="w-14 sm:w-16 px-2 py-1 text-xs bg-slate-900 border border-slate-600 rounded text-slate-200"
                                  />
                                  <button
                                    onClick={handleSaveModify}
                                    className="px-2 py-1 text-xs bg-green-500/20 text-green-400 rounded hover:bg-green-500/30 transition-colors whitespace-nowrap shrink-0"
                                    disabled={modifyOrderMutation.isLoading}
                                  >
                                    Save
                                  </button>
                                  <button
                                    onClick={() => {
                                      setEditingOrder(null)
                                      setModifyPrice('')
                                      setModifyQuantity('')
                                    }}
                                    className="px-2 py-1 text-xs bg-slate-600 text-slate-300 rounded hover:bg-slate-500 transition-colors whitespace-nowrap shrink-0"
                                  >
                                    Cancel
                                  </button>
                                </>
                              ) : (
                                <>
                                  <button
                                    onClick={() => handleStartEdit(order)}
                                    className="px-2 py-1 text-xs bg-blue-500/20 text-blue-400 rounded hover:bg-blue-500/30 transition-colors flex items-center gap-1"
                                    disabled={modifyOrderMutation.isLoading}
                                  >
                                    <Edit className="w-3 h-3" />
                                    Modify
                                  </button>
                                  <button
                                    onClick={() => handleCancelOrder(order.id)}
                                    className="px-3 py-1 text-xs bg-red-500/20 text-red-400 rounded hover:bg-red-500/30 transition-colors flex items-center gap-1"
                                    disabled={cancelOrderMutation.isLoading}
                                  >
                                    <X className="w-3 h-3" />
                                    Cancel
                                  </button>
                                </>
                              )}
                            </div>
                          )}
                        </div>
                        <div className="grid grid-cols-1 gap-4 text-sm sm:grid-cols-4">
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
                            <p className="text-slate-400">Stop</p>
                            <p className="font-semibold">
                              {stopPrice ? `$${stopPrice.toFixed(2)}` : '—'}
                            </p>
                          </div>
                          <div>
                            <p className="text-slate-400">Bracket</p>
                            <p className="font-semibold text-xs text-slate-200">{bracketSummary}</p>
                          </div>
                          <div>
                            <p className="text-slate-400">Time in Force</p>
                            <p className="font-semibold text-slate-200">{order.time_in_force || 'DAY'}</p>
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
          )}
          </div>
        </>
        )}
        </div>
      </div>
    </div>
  )
}
