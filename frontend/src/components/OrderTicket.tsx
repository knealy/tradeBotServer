import { useState, FormEvent } from 'react'
import { useMutation, useQueryClient } from 'react-query'
import { orderApi, positionApi } from '../services/api'
import { useAccount } from '../contexts/AccountContext'
import type { PlaceOrderPayload } from '../types'
import { Loader2, CheckCircle2, AlertCircle, ChevronDown, ChevronUp } from 'lucide-react'

interface OrderTicketProps {
  onOrderPlaced?: () => void
}

type OrderSide = 'BUY' | 'SELL'
type OrderTypeOption = 'market' | 'limit' | 'stop'

const defaultSymbol = 'MNQ'

export default function OrderTicket({ onOrderPlaced }: OrderTicketProps) {
  const queryClient = useQueryClient()
  const { selectedAccount } = useAccount()
  const [symbol, setSymbol] = useState(defaultSymbol)
  const [side, setSide] = useState<OrderSide>('BUY')
  const [orderType, setOrderType] = useState<OrderTypeOption>('market')
  const [quantity, setQuantity] = useState('1')
  const [limitPrice, setLimitPrice] = useState('')
  const [stopPrice, setStopPrice] = useState('')
  const [enableBracket, setEnableBracket] = useState(false)
  const [stopLossTicks, setStopLossTicks] = useState('')
  const [takeProfitTicks, setTakeProfitTicks] = useState('')
  const [stopLossPrice, setStopLossPrice] = useState('')
  const [takeProfitPrice, setTakeProfitPrice] = useState('')
  const [enableBreakeven, setEnableBreakeven] = useState(false)
  const [reduceOnly, setReduceOnly] = useState(false)
  const [timeInForce, setTimeInForce] = useState<'DAY' | 'GTC'>('DAY')
  const [formError, setFormError] = useState<string | null>(null)
  const [formSuccess, setFormSuccess] = useState<string | null>(null)

  const accountId = selectedAccount?.id

  const placeOrderMutation = useMutation(orderApi.placeOrder, {
    onSuccess: () => {
      setFormSuccess('Order submitted successfully')
      setFormError(null)
      queryClient.invalidateQueries(['orders', accountId])
      queryClient.invalidateQueries(['positions', accountId])
      if (onOrderPlaced) onOrderPlaced()
    },
    onError: (error: any) => {
      console.error('Failed to submit order', error)
      const apiError = error?.response?.data?.error || error?.message || 'Failed to submit order'
      setFormError(apiError)
      setFormSuccess(null)
    },
  })

  const cancelAllMutation = useMutation((acctId?: string) => orderApi.cancelAll(acctId), {
    onSuccess: () => {
      queryClient.invalidateQueries(['orders', accountId])
      setFormSuccess('Cancelled all open orders')
      setFormError(null)
    },
    onError: (error: any) => {
      const apiError = error?.response?.data?.error || error?.message || 'Failed to cancel orders'
      setFormError(apiError)
      setFormSuccess(null)
    },
  })

  const flattenMutation = useMutation(positionApi.flattenAll, {
    onSuccess: () => {
      queryClient.invalidateQueries(['positions', accountId])
      queryClient.invalidateQueries(['orders', accountId])
      setFormSuccess('Flatten command sent')
      setFormError(null)
    },
    onError: (error: any) => {
      const apiError = error?.response?.data?.error || error?.message || 'Failed to flatten positions'
      setFormError(apiError)
      setFormSuccess(null)
    },
  })

  const resetForm = () => {
    setLimitPrice('')
    setStopPrice('')
    setStopLossTicks('')
    setTakeProfitTicks('')
    setStopLossPrice('')
    setTakeProfitPrice('')
    setEnableBracket(false)
    setEnableBreakeven(false)
    setReduceOnly(false)
    setTimeInForce('DAY')
  }

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!accountId) {
      setFormError('Select an account before placing orders')
      setFormSuccess(null)
      return
    }

    if (!symbol.trim()) {
      setFormError('Symbol is required')
      setFormSuccess(null)
      return
    }

    const quantityValue = Number(quantity)
    if (!Number.isFinite(quantityValue) || quantityValue <= 0) {
      setFormError('Quantity must be greater than 0')
      setFormSuccess(null)
      return
    }

    const payload: PlaceOrderPayload = {
      symbol,
      side,
      quantity: Math.floor(quantityValue),
      order_type: orderType,
      account_id: accountId,
      time_in_force: timeInForce,
      reduce_only: reduceOnly,
    }

    if (orderType === 'limit') {
      const lp = Number(limitPrice)
      if (!Number.isFinite(lp) || lp <= 0) {
        setFormError('Limit price required for limit orders')
        setFormSuccess(null)
        return
      }
      payload.limit_price = lp
    }

    if (orderType === 'stop') {
      const sp = Number(stopPrice)
      if (!Number.isFinite(sp) || sp <= 0) {
        setFormError('Stop price required for stop orders')
        setFormSuccess(null)
        return
      }
      payload.stop_price = sp
    }

    if (enableBracket) {
      if (orderType === 'stop') {
        const slPrice = stopLossPrice ? Number(stopLossPrice) : undefined
        const tpPrice = takeProfitPrice ? Number(takeProfitPrice) : undefined

        if (!slPrice || !tpPrice) {
          setFormError('Stop loss and take profit prices are required for stop-entry brackets')
          setFormSuccess(null)
          return
        }

        payload.stop_loss_price = slPrice
        payload.take_profit_price = tpPrice
        payload.enable_bracket = true
        payload.enable_breakeven = enableBreakeven
      } else {
        const slTicks = stopLossTicks ? Number(stopLossTicks) : undefined
        const tpTicks = takeProfitTicks ? Number(takeProfitTicks) : undefined

        if (!slTicks && !tpTicks) {
          setFormError('Provide stop-loss and/or take-profit ticks for bracket orders')
          setFormSuccess(null)
          return
        }

        if (slTicks) payload.stop_loss_ticks = Math.floor(slTicks)
        if (tpTicks) payload.take_profit_ticks = Math.floor(tpTicks)
        payload.enable_bracket = true
      }
    }

    placeOrderMutation.mutate(payload, {
      onSuccess: () => {
        resetForm()
      },
    })
  }

  const isSubmitting = placeOrderMutation.isLoading
  const disableActions = !accountId
  const [isOpen, setIsOpen] = useState(true)

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl shadow-sm">
      <button
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        className="w-full flex items-center justify-between px-4 py-3"
      >
        <div className="flex items-center gap-3 text-left">
          <div>
            <p className="text-sm font-semibold text-slate-200">Quick Order Ticket</p>
            <p className="text-xs text-slate-400">Place discretionary trades directly from the dashboard</p>
          </div>
        </div>
        {isOpen ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
      </button>

      {isOpen && (
        <div className="px-4 pb-4">
          {formError && (
        <div className="mb-4 flex items-center gap-2 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          <AlertCircle className="h-4 w-4" />
          <span>{formError}</span>
        </div>
      )}

      {formSuccess && (
        <div className="mb-4 flex items-center gap-2 rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-200">
          <CheckCircle2 className="h-4 w-4" />
          <span>{formSuccess}</span>
        </div>
      )}

      <form className="space-y-5" onSubmit={handleSubmit}>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
          <div>
            <label className="block text-xs uppercase tracking-wide text-slate-400">Symbol</label>
            <input
              type="text"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              className="mt-1 w-full rounded bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none ring-1 ring-slate-700 focus:ring-primary-500"
              placeholder="MNQ"
            />
          </div>

          <div>
            <label className="block text-xs uppercase tracking-wide text-slate-400">Quantity</label>
            <input
              type="number"
              min={1}
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              className="mt-1 w-full rounded bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none ring-1 ring-slate-700 focus:ring-primary-500"
            />
          </div>

          <div>
            <label className="block text-xs uppercase tracking-wide text-slate-400">Side</label>
            <div className="mt-1 grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setSide('BUY')}
                className={`rounded px-3 py-2 text-sm font-semibold transition-colors ${
                  side === 'BUY'
                    ? 'bg-emerald-500/30 text-emerald-200 ring-1 ring-emerald-400/60'
                    : 'bg-slate-900 text-slate-300 hover:bg-slate-700/60'
                }`}
              >
                Buy
              </button>
              <button
                type="button"
                onClick={() => setSide('SELL')}
                className={`rounded px-3 py-2 text-sm font-semibold transition-colors ${
                  side === 'SELL'
                    ? 'bg-red-500/30 text-red-200 ring-1 ring-red-400/60'
                    : 'bg-slate-900 text-slate-300 hover:bg-slate-700/60'
                }`}
              >
                Sell
              </button>
            </div>
          </div>

          <div>
            <label className="block text-xs uppercase tracking-wide text-slate-400">Order Type</label>
            <select
              value={orderType}
              onChange={(e) => setOrderType(e.target.value as OrderTypeOption)}
              className="mt-1 w-full rounded bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none ring-1 ring-slate-700 focus:ring-primary-500"
            >
              <option value="market">Market</option>
              <option value="limit">Limit</option>
              <option value="stop">Stop Entry</option>
            </select>
          </div>
        </div>

        {orderType === 'limit' && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div>
              <label className="block text-xs uppercase tracking-wide text-slate-400">Limit Price</label>
              <input
                type="number"
                value={limitPrice}
                onChange={(e) => setLimitPrice(e.target.value)}
                className="mt-1 w-full rounded bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none ring-1 ring-slate-700 focus:ring-primary-500"
                placeholder="Enter price"
              />
            </div>
          </div>
        )}

        {orderType === 'stop' && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div>
              <label className="block text-xs uppercase tracking-wide text-slate-400">Stop Price</label>
              <input
                type="number"
                value={stopPrice}
                onChange={(e) => setStopPrice(e.target.value)}
                className="mt-1 w-full rounded bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none ring-1 ring-slate-700 focus:ring-primary-500"
                placeholder="Entry stop price"
              />
            </div>
          </div>
        )}

        <div className="rounded border border-slate-700/60 bg-slate-900/40 p-4">
          <div className="flex items-center justify-between">
            <label className="flex items-center gap-2 text-sm font-semibold text-slate-200">
              <input
                type="checkbox"
                checked={enableBracket}
                onChange={(e) => setEnableBracket(e.target.checked)}
                className="h-4 w-4 rounded border-slate-600 bg-slate-800 text-primary-500 focus:ring-primary-500"
              />
              Attach Bracket
            </label>
            <span className="text-xs uppercase tracking-wide text-slate-500">Optional</span>
          </div>

          {enableBracket && orderType !== 'stop' && (
            <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className="block text-xs uppercase tracking-wide text-slate-400">Stop Loss (ticks)</label>
                <input
                  type="number"
                  value={stopLossTicks}
                  onChange={(e) => setStopLossTicks(e.target.value)}
                  className="mt-1 w-full rounded bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none ring-1 ring-slate-700 focus:ring-primary-500"
                  placeholder="e.g. 40"
                />
              </div>
              <div>
                <label className="block text-xs uppercase tracking-wide text-slate-400">Take Profit (ticks)</label>
                <input
                  type="number"
                  value={takeProfitTicks}
                  onChange={(e) => setTakeProfitTicks(e.target.value)}
                  className="mt-1 w-full rounded bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none ring-1 ring-slate-700 focus:ring-primary-500"
                  placeholder="e.g. 80"
                />
              </div>
            </div>
          )}

          {enableBracket && orderType === 'stop' && (
            <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div>
                <label className="block text-xs uppercase tracking-wide text-slate-400">Stop Loss Price</label>
                <input
                  type="number"
                  value={stopLossPrice}
                  onChange={(e) => setStopLossPrice(e.target.value)}
                  className="mt-1 w-full rounded bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none ring-1 ring-slate-700 focus:ring-primary-500"
                  placeholder="Stop loss price"
                />
              </div>
              <div>
                <label className="block text-xs uppercase tracking-wide text-slate-400">Take Profit Price</label>
                <input
                  type="number"
                  value={takeProfitPrice}
                  onChange={(e) => setTakeProfitPrice(e.target.value)}
                  className="mt-1 w-full rounded bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none ring-1 ring-slate-700 focus:ring-primary-500"
                  placeholder="Take profit price"
                />
              </div>
              <div className="flex items-center">
                <label className="flex items-center gap-2 text-sm font-medium text-slate-200">
                  <input
                    type="checkbox"
                    checked={enableBreakeven}
                    onChange={(e) => setEnableBreakeven(e.target.checked)}
                    className="h-4 w-4 rounded border-slate-600 bg-slate-800 text-primary-500 focus:ring-primary-500"
                  />
                  Enable Breakeven
                </label>
              </div>
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div>
            <label className="block text-xs uppercase tracking-wide text-slate-400">Time in Force</label>
            <select
              value={timeInForce}
              onChange={(e) => setTimeInForce(e.target.value as 'DAY' | 'GTC')}
              className="mt-1 w-full rounded bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none ring-1 ring-slate-700 focus:ring-primary-500"
            >
              <option value="DAY">DAY</option>
              <option value="GTC">GTC</option>
            </select>
          </div>
          <div className="flex items-end">
            <label className="flex items-center gap-2 text-sm font-medium text-slate-200">
              <input
                type="checkbox"
                checked={reduceOnly}
                onChange={(e) => setReduceOnly(e.target.checked)}
                className="h-4 w-4 rounded border-slate-600 bg-slate-800 text-primary-500 focus:ring-primary-500"
              />
              Reduce-Only
            </label>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 pt-2">
          <button
            type="submit"
            disabled={isSubmitting || disableActions}
            className="inline-flex items-center gap-2 rounded bg-primary-500 px-4 py-2 text-sm font-semibold text-slate-950 transition-colors hover:bg-primary-400 disabled:cursor-not-allowed disabled:bg-slate-600 disabled:text-slate-300"
          >
            {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
            Place Order
          </button>
          <button
            type="button"
            onClick={() => cancelAllMutation.mutate(accountId)}
            disabled={cancelAllMutation.isLoading || disableActions}
            className="inline-flex items-center gap-2 rounded border border-slate-600 px-4 py-2 text-sm font-semibold text-slate-200 transition-colors hover:bg-slate-700 disabled:cursor-not-allowed disabled:text-slate-400"
          >
            {cancelAllMutation.isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
            Cancel All Orders
          </button>
          <button
            type="button"
            onClick={() => flattenMutation.mutate()}
            disabled={flattenMutation.isLoading || disableActions}
            className="inline-flex items-center gap-2 rounded border border-red-600/60 px-4 py-2 text-sm font-semibold text-red-300 transition-colors hover:bg-red-500/10 disabled:cursor-not-allowed disabled:text-red-500/60"
          >
            {flattenMutation.isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
            Flatten Positions
          </button>
        </div>

        <p className="text-xs text-slate-500">
          Tip: Brackets on market/limit orders use tick offsets. Stop entries require explicit pricing for the attached OCO legs.
        </p>
      </form>
        </div>
      )}
    </div>
  )
}


