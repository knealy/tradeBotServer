import { useEffect } from 'react'
import { useQueryClient } from 'react-query'
import { wsService } from '../services/websocket'
import { useAccount } from '../contexts/AccountContext'
import { useMarketSocket } from '../hooks/useMarketSocket'
import type { Account } from '../types'
import PositionsOverview from './PositionsOverview'
import PerformanceChart from './PerformanceChart'
import TradesTable from './TradesTable'
import RiskDrawer from './RiskDrawer'

export default function Dashboard() {
  const queryClient = useQueryClient()
  const { selectedAccount } = useAccount()
  
  // Enable live market updates for positions/orders
  useMarketSocket()

  // Account ID for WebSocket updates
  const accountId = selectedAccount?.id


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
        queryClient.setQueryData<Account[] | undefined>(
          ['accounts'],
          (previous) =>
            previous?.map((acct) =>
              (acct.id || acct.accountId || acct.account_id) === normalized.id
                ? { ...acct, ...normalized }
                : acct
            )
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

  return (
    <div className="bg-slate-800/30 border border-slate-700/50 rounded-2xl p-6 sm:p-8 backdrop-blur-sm">
      {/* Main Dashboard Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Left Column - Charts */}
        <div className="lg:col-span-2 space-y-5">
          <PerformanceChart />
          <PositionsOverview />
        </div>

        {/* Right Column - Risk and Trades */}
        <div className="space-y-5">
          <RiskDrawer />
          <TradesTable />
        </div>
      </div>
    </div>
  )
}

