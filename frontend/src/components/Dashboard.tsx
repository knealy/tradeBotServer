import { useEffect, useMemo } from 'react'
import { useQuery, useQueryClient } from 'react-query'
import { accountApi, metricsApi } from '../services/api'
import { wsService } from '../services/websocket'
import { useAccount } from '../contexts/AccountContext'
import { useWebSocket } from '../contexts/WebSocketContext'
import AccountCard from './AccountCard'
import AccountSelector from './AccountSelector'
import MetricsCard from './MetricsCard'
import PositionsOverview from './PositionsOverview'
import PerformanceChart from './PerformanceChart'

export default function Dashboard() {
  const queryClient = useQueryClient()
  const { accounts, selectedAccount, setSelectedAccount } = useAccount()
  const { status: socketStatus, reconnectAttempts, lastError: socketError, reconnect: reconnectSocket } = useWebSocket()

  // Fetch account info (less frequent)
  const { data: accountInfo } = useQuery('accountInfo', accountApi.getAccountInfo, {
    enabled: !!selectedAccount,
    refetchInterval: 15000, // Refetch every 15 seconds (was 5)
    staleTime: 10000, // Consider data fresh for 10 seconds
  })

  // Fetch metrics (less frequent)
  const { data: metricsData } = useQuery('metrics', metricsApi.getMetrics, {
    refetchInterval: 30000, // Refetch every 30 seconds (was 10)
    staleTime: 20000, // Consider data fresh for 20 seconds
  })
  
  // Extract metrics from response
  const metrics = metricsData?.performance || metricsData

  // WebSocket connection for real-time updates
  useEffect(() => {
    let isComponentMounted = true

    // Handle WebSocket messages with React Query cache invalidation
    const handleAccountUpdate = (data: any) => {
      if (!isComponentMounted) return
      console.log('✅ Account update via WebSocket:', data)
      queryClient.invalidateQueries(['accountInfo'])
      queryClient.invalidateQueries(['accounts'])
    }

    const handleMetricsUpdate = (data: any) => {
      if (!isComponentMounted) return
      console.log('✅ Metrics update via WebSocket:', data)
      queryClient.invalidateQueries(['metrics'])
    }

    const handlePositionUpdate = (data: any) => {
      if (!isComponentMounted) return
      console.log('✅ Position update via WebSocket:', data)
      queryClient.invalidateQueries(['positions'])
    }

    const handleAccountsUpdate = (data: any) => {
      if (!isComponentMounted) return
      console.log('✅ Accounts update via WebSocket:', data)
      queryClient.invalidateQueries(['accounts'])
    }

    // Register event handlers
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
  }, [queryClient])

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
        {/* Left Column - Account Info & Positions */}
        <div className="lg:col-span-2 space-y-6">
          {accountInfo && (
            <AccountCard account={accountInfo} isSelected={true} />
          )}
          <PositionsOverview />
          <PerformanceChart />
        </div>

        {/* Right Column - Metrics */}
        <div className="space-y-6">
          {metrics && <MetricsCard metrics={metrics} />}
        </div>
      </div>
    </div>
  )
}

