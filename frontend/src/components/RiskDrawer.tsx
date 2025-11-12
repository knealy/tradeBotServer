import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from 'react-query'
import { ShieldAlert, ShieldCheck, AlertTriangle, CheckCircle2, ChevronDown, ChevronUp, Loader2 } from 'lucide-react'
import { useAccount } from '../contexts/AccountContext'
import { riskApi } from '../services/api'
import { wsService } from '../services/websocket'
import type { RiskSnapshot, RiskEvent } from '../types'

const currencyFormatter = new Intl.NumberFormat(undefined, {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
})

const percentFormatter = new Intl.NumberFormat(undefined, {
  style: 'percent',
  minimumFractionDigits: 0,
  maximumFractionDigits: 1,
})

const formatCurrency = (value: number | null | undefined) => {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '—'
  }
  return currencyFormatter.format(value)
}

const formatDateTime = (isoTimestamp?: string) => {
  if (!isoTimestamp) return '—'
  try {
    return new Date(isoTimestamp).toLocaleString()
  } catch {
    return isoTimestamp
  }
}

const getBarClass = (violated: boolean, pct: number) => {
  if (violated || pct >= 1) return 'bg-red-500'
  if (pct >= 0.8) return 'bg-amber-500'
  return 'bg-emerald-500'
}

const getBadgeClass = (compliant: boolean) =>
  compliant ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : 'bg-red-500/20 text-red-200 border border-red-500/30'

const eventBadgeClass: Record<RiskEvent['level'], string> = {
  info: 'bg-slate-700 text-slate-200 border border-slate-600',
  warning: 'bg-amber-500/20 text-amber-300 border border-amber-500/40',
  error: 'bg-red-500/20 text-red-200 border border-red-500/40',
  success: 'bg-emerald-500/20 text-emerald-200 border border-emerald-500/40',
}

export default function RiskDrawer() {
  const { selectedAccount } = useAccount()
  const accountId = selectedAccount?.id
  const queryClient = useQueryClient()
  const [isOpen, setIsOpen] = useState(true)

  const { data, isLoading, error, refetch, isFetching } = useQuery<RiskSnapshot>(
    ['risk', accountId],
    () => riskApi.getRisk(accountId),
    {
      enabled: !!accountId,
      staleTime: 30_000,
      refetchInterval: 60_000,
      keepPreviousData: true,
    }
  )
  const hasError = Boolean(error)
  const errorMessage = error instanceof Error ? error.message : 'Failed to load risk metrics.'

  useEffect(() => {
    const handler = (payload: RiskSnapshot) => {
      if (!payload) return
      if (accountId && payload.account_id && String(payload.account_id) !== String(accountId)) {
        return
      }
      queryClient.setQueryData(['risk', accountId], payload)
    }
    wsService.on('risk_update', handler)
    return () => {
      wsService.off('risk_update', handler)
    }
  }, [accountId, queryClient])

  const dllSummary = useMemo(() => {
    if (!data) return null
    const pct = data.dll.pct ?? (data.dll.limit ? data.dll.used / data.dll.limit : 0)
    return {
      pct: Math.min(1, Math.max(0, pct || 0)),
      limit: data.dll.limit,
      used: data.dll.used,
      remaining: data.dll.remaining,
      violated: data.dll.violated,
    }
  }, [data])

  const mllSummary = useMemo(() => {
    if (!data) return null
    const pct = data.mll.pct ?? (data.mll.limit ? data.mll.used / data.mll.limit : 0)
    return {
      pct: Math.min(1, Math.max(0, pct || 0)),
      limit: data.mll.limit,
      used: data.mll.used,
      remaining: data.mll.remaining,
      violated: data.mll.violated,
    }
  }, [data])

  const events = data?.events ?? []

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl shadow-sm">
      <button
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        className="w-full flex items-center justify-between px-4 py-3"
      >
        <div className="flex items-center gap-3 text-left">
          {data?.compliance ? (
            <ShieldCheck className="w-5 h-5 text-emerald-400" />
          ) : (
            <ShieldAlert className="w-5 h-5 text-red-400" />
          )}
          <div>
            <p className="text-sm font-semibold text-slate-200">Risk Monitor</p>
            <p className="text-xs text-slate-400">
              {data?.account_name ? `${data.account_name} • ` : ''}
              {data ? formatDateTime(data.timestamp) : 'Awaiting data'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isFetching && <Loader2 className="w-4 h-4 animate-spin text-slate-400" />}
          {isOpen ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
        </div>
      </button>

      {isOpen && (
        <div className="px-4 pb-4 space-y-4">
          {isLoading && (
            <div className="flex items-center gap-2 text-sm text-slate-400">
              <Loader2 className="w-4 h-4 animate-spin" />
              Loading risk metrics...
            </div>
          )}

          {hasError && (
            <div className="flex items-center justify-between gap-3 text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2">
              <span>{errorMessage}</span>
              <button
                onClick={() => refetch()}
                className="text-xs font-semibold text-red-200 underline"
              >
                Retry
              </button>
            </div>
          )}

          {!isLoading && !hasError && data && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <span className={`text-xs font-semibold px-2 py-1 rounded-full ${getBadgeClass(data.compliance)}`}>
                  {data.compliance ? 'Compliant' : 'Non-Compliant'}
                </span>
                {data.violations.length > 0 && (
                  <div className="flex items-center gap-2 text-xs text-red-300">
                    <AlertTriangle className="w-4 h-4" />
                    {data.violations[0]}
                  </div>
                )}
              </div>

              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <p className="text-slate-400 text-xs uppercase mb-1">Current Balance</p>
                  <p className="font-semibold text-slate-100">{formatCurrency(data.balance)}</p>
                </div>
                <div>
                  <p className="text-slate-400 text-xs uppercase mb-1">Total PnL</p>
                  <p className={`font-semibold ${data.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {formatCurrency(data.total_pnl)}
                  </p>
                </div>
                <div>
                  <p className="text-slate-400 text-xs uppercase mb-1">Start Balance</p>
                  <p className="font-semibold text-slate-100">{formatCurrency(data.start_balance)}</p>
                </div>
                <div>
                  <p className="text-slate-400 text-xs uppercase mb-1">Trailing Loss</p>
                  <p className={`font-semibold ${data.trailing_loss > 0 ? 'text-amber-300' : 'text-slate-200'}`}>
                    {formatCurrency(data.trailing_loss)}
                  </p>
                </div>
              </div>

              {dllSummary && (
                <div className="space-y-1">
                  <div className="flex items-center justify-between text-xs text-slate-400">
                    <span>Daily Loss Limit</span>
                    <span>
                      {formatCurrency(dllSummary.used)} / {formatCurrency(dllSummary.limit)}
                    </span>
                  </div>
                  <div className="h-2 rounded bg-slate-700 overflow-hidden">
                    <div
                      className={`h-full ${getBarClass(dllSummary.violated, dllSummary.pct)}`}
                      style={{ width: `${dllSummary.pct * 100}%` }}
                    />
                  </div>
                  <p className="text-xs text-slate-500">
                    {dllSummary.limit === null
                      ? 'No daily loss limit configured'
                      : `Remaining: ${formatCurrency(dllSummary.remaining)} (${percentFormatter.format(1 - dllSummary.pct)})`}
                  </p>
                </div>
              )}

              {mllSummary && (
                <div className="space-y-1">
                  <div className="flex items-center justify-between text-xs text-slate-400">
                    <span>Maximum Loss Limit</span>
                    <span>
                      {formatCurrency(mllSummary.used)} / {formatCurrency(mllSummary.limit)}
                    </span>
                  </div>
                  <div className="h-2 rounded bg-slate-700 overflow-hidden">
                    <div
                      className={`h-full ${getBarClass(mllSummary.violated, mllSummary.pct)}`}
                      style={{ width: `${mllSummary.pct * 100}%` }}
                    />
                  </div>
                  <p className="text-xs text-slate-500">
                    {mllSummary.limit === null
                      ? 'No maximum loss limit configured'
                      : `Remaining: ${formatCurrency(mllSummary.remaining)} (${percentFormatter.format(1 - mllSummary.pct)})`}
                  </p>
                </div>
              )}

              <div className="space-y-2">
                <div className="flex items-center gap-2 text-xs text-slate-400 uppercase">
                  <CheckCircle2 className="w-4 h-4 text-slate-300" />
                  Recent Risk Events
                </div>
                {events.length === 0 ? (
                  <div className="text-xs text-slate-500 bg-slate-900/60 border border-slate-700/60 rounded-lg px-3 py-2">
                    No recent risk events
                  </div>
                ) : (
                  <div className="space-y-2 max-h-40 overflow-y-auto pr-1">
                    {events.map((event) => (
                      <div key={event.id} className={`px-3 py-2 rounded-lg text-xs ${eventBadgeClass[event.level]}`}>
                        <div className="flex items-center justify-between mb-1">
                          <span className="font-semibold">{event.message}</span>
                          <span className="text-slate-300/80">{formatDateTime(event.timestamp)}</span>
                        </div>
                        {event.meta && (
                          <pre className="text-xs text-slate-300/70 whitespace-pre-wrap">
                            {JSON.stringify(event.meta, null, 2)}
                          </pre>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}


