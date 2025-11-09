import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from 'react-query'
import { accountApi, metricsApi } from '../services/api'
import { wsService } from '../services/websocket'
import type { Account, PerformanceMetrics } from '../types'
import AccountCard from './AccountCard'
import AccountSelector from './AccountSelector'
import MetricsCard from './MetricsCard'
import PositionsOverview from './PositionsOverview'
import PerformanceChart from './PerformanceChart'

export default function Dashboard() {
  const [selectedAccount, setSelectedAccount] = useState<Account | null>(null)
  const [connectionStatus, setConnectionStatus] = useState<'connected' | 'disconnected'>('disconnected')
  const queryClient = useQueryClient()

  // Fetch accounts (less frequent - balances don't change that often)
  const { data: accounts = [] } = useQuery('accounts', accountApi.getAccounts, {
    refetchInterval: 60000, // Refetch every 60 seconds (was 30)
    staleTime: 30000, // Consider data fresh for 30 seconds
  })

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
    let checkConnection: NodeJS.Timeout | null = null
    let isComponentMounted = true
    
    // Connect WebSocket only if not already connected
    if (!wsService.isConnected()) {
      wsService.connect()
    }
    
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

    // Check connection status periodically
    checkConnection = setInterval(() => {
      if (isComponentMounted) {
        setConnectionStatus(wsService.isConnected() ? 'connected' : 'disconnected')
      }
    }, 1000)

    return () => {
      isComponentMounted = false
      if (checkConnection) {
        clearInterval(checkConnection)
      }
      // Unregister event handlers
      wsService.off('account_update', handleAccountUpdate)
      wsService.off('metrics_update', handleMetricsUpdate)
      wsService.off('position_update', handlePositionUpdate)
      wsService.off('accounts_update', handleAccountsUpdate)
      // Don't disconnect - keep connection alive for other components
    }
  }, [])

  // Auto-select first account
  useEffect(() => {
    if (accounts.length > 0 && !selectedAccount) {
      setSelectedAccount(accounts[0])
    }
  }, [accounts, selectedAccount])

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Dashboard</h1>
          <p className="text-slate-400 mt-1">Real-time trading overview</p>
        </div>
        <div className="flex items-center gap-4">
          <div className={`flex items-center gap-2 px-4 py-2 rounded-lg ${
            connectionStatus === 'connected' 
              ? 'bg-green-500/20 text-green-400' 
              : 'bg-red-500/20 text-red-400'
          }`}>
            <div className={`w-2 h-2 rounded-full ${
              connectionStatus === 'connected' ? 'bg-green-400' : 'bg-red-400'
            }`} />
            <span className="text-sm font-medium">
              {connectionStatus === 'connected' ? 'Connected' : 'Disconnected'}
            </span>
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

