import { useEffect, useMemo } from 'react'
import { useQuery, useQueryClient } from 'react-query'
import { accountApi, metricsApi } from '../services/api'
import { wsService } from '../services/websocket'
import { useAccount } from '../contexts/AccountContext'
import { useWebSocket } from '../contexts/WebSocketContext'
import type { Account } from '../types'
import AccountCard from './AccountCard'
import AccountSelector from './AccountSelector'
import MetricsCard from './MetricsCard'
import PositionsOverview from './PositionsOverview'
import PerformanceChart from './PerformanceChart'
import HistoricalPriceChart from './HistoricalPriceChart'
import TradesTable from './TradesTable'

export default function Dashboard() {
  const queryClient = useQueryClient()
  const { accounts, selectedAccount, setSelectedAccount } = useAccount()
  const { status: socketStatus, reconnectAttempts, lastError: socketError, reconnect: reconnectSocket } = useWebSocket()

  // Fetch account info (less frequent)
  const accountId = selectedAccount?.id
  const { data: accountInfo } = useQuery(
    ['accountInfo', accountId],
    accountApi.getAccountInfo,
    {
      enabled: !!accountId,
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    }
  )

  // Fetch metrics (fallback polling)
  const { data: metricsData } = useQuery(
    ['metrics'],
    metricsApi.getMetrics,
    {
      refetchInterval: socketStatus === 'connected' ? false : 60_000,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    }
  )
  
  // Extract metrics from response
  const metrics = metricsData?.performance || metricsData

  // WebSocket connection for real-time updates
  useEffect(() => {
    let isComponentMounted = true

    const handleAccountUpdate = (data: any) => {
      if (!isComponentMounted || !data) return
      const normalized = {
        id: data.account_id || data.accountId || data.id,
        name: data.account_name || data.name,
        status: data.status || 'active',
        balance: Number(data.balance ?? 0),
        currency: data.currency || 'USD',
        account_type: data.account_type || 'unknown',
        equity: Number(data.equity ?? data.balance ?? 0),
        dailyPnL: Number(data.dailyPnL ?? data.daily_pnl ?? 0),
        daily_pnl: Number(data.dailyPnL ?? data.daily_pnl ?? 0),
      }

      if (normalized.id) {
        queryClient.setQueryData(['accountInfo', normalized.id], normalized)
        queryClient.setQueryData<Account[]>(
          ['accounts'],
          (previous) =>
            previous?.map((acct) =>
              (acct.id || acct.accountId || acct.account_id) === normalized.id
                ? { ...acct, ...normalized }
                : acct
            ) || previous
        )
      }
    }

    const handleMetricsUpdate = (data: any) => {
      if (!isComponentMounted || !data) return
      const payload = data.performance || data
      queryClient.setQueryData(['metrics'], payload)
    }

    const handlePositionUpdate = (data: any) => {
      if (!isComponentMounted || !data) return
      if (accountId) {
        queryClient.setQueryData(['positions', accountId], data)
      }
    }

    const handleAccountsUpdate = (data: any) => {
      if (!isComponentMounted || !data) return
      if (Array.isArray(data)) {
        queryClient.setQueryData(['accounts'], data)
      }
    }

    wsService.on('account_update', handleAccountUpdate)
    wsService.on('metrics_update', handleMetricsUpdate)
    wsService.on('position_update', handlePositionUpdate)
    wsService.on('accounts_update', handleAccountsUpdate)

    return () => {
      isComponentMounted = false
      wsService.off('account_update', handleAccountUpdate)
      wsService.off('metrics_update', handleMetricsUpdate)
      wsService.off('position_update', handlePositionUpdate)
      wsService.off('accounts_update', handleAccountsUpdate)
    }
  }, [queryClient, accountId])

  const connectionBadge = useMemo(() => {
    switch (socketStatus) {
      case 'connected':
        return {
          label: 'Connected',
          container: 'bg-green-500/20 text-green-300',
          dot: 'bg-green-400',
        }
      case 'reconnecting':
        return {
          label: `Reconnecting (${reconnectAttempts})`,
          container: 'bg-amber-500/20 text-amber-200',
          dot: 'bg-amber-300',
        }
      case 'error':
        return {
          label: 'Connection Error',
          container: 'bg-red-500/20 text-red-300',
          dot: 'bg-red-400',
        }
      default:
        return {
          label: 'Disconnected',
          container: 'bg-red-500/20 text-red-300',
          dot: 'bg-red-400',
        }
    }
  }, [socketStatus, reconnectAttempts])

  const showRetryButton = socketStatus === 'error' || socketStatus === 'disconnected'

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Dashboard</h1>
          <p className="text-slate-400 mt-1">Real-time trading overview</p>
        </div>
        <div className="flex items-center gap-4">
          <div className={`flex items-center gap-3 px-4 py-2 rounded-lg ${connectionBadge.container}`}>
            <div className={`w-2 h-2 rounded-full ${connectionBadge.dot}`} />
            <div className="flex flex-col">
              <span className="text-sm font-medium">{connectionBadge.label}</span>
              {socketError && (
                <span className="text-xs text-red-200 truncate max-w-[200px]">
                  {socketError}
                </span>
              )}
            </div>
            {showRetryButton && (
              <button
                onClick={reconnectSocket}
                className="ml-2 text-xs font-semibold text-primary-300 underline"
              >
                Retry
              </button>
            )}
          </div>
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

      {/* Main Dashboard Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Column - Account Info & Charts */}
        <div className="lg:col-span-2 space-y-6">
          {accountInfo && (
            <AccountCard account={accountInfo} isSelected={true} />
          )}
          <PerformanceChart />
          <HistoricalPriceChart />
          <PositionsOverview />
        </div>

        {/* Right Column - Metrics and Trades */}
        <div className="space-y-6">
          {metrics && <MetricsCard metrics={metrics} />}
          <TradesTable />
        </div>
      </div>
    </div>
  )
}

