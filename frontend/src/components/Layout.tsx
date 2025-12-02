import { ReactNode, useMemo } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Activity, TrendingUp, Settings, BarChart3 } from 'lucide-react'
import { useAccount } from '../contexts/AccountContext'
import { useWebSocket } from '../contexts/WebSocketContext'
import { useQuery } from 'react-query'
import { metricsApi } from '../services/api'
import AccountSelector from './AccountSelector'
import NotificationsFeed from './NotificationsFeed'
import MetricsCard from './MetricsCard'

interface LayoutProps {
  children: ReactNode
}

export default function Layout({ children }: LayoutProps) {
  const location = useLocation()
  const { accounts, selectedAccount, setSelectedAccount } = useAccount()
  const { status: socketStatus, reconnectAttempts, lastError: socketError, reconnect: reconnectSocket } = useWebSocket()

  // Fetch metrics for MetricsCard
  const { data: metricsData } = useQuery(
    ['metrics'],
    metricsApi.getMetrics,
    {
      refetchInterval: socketStatus === 'connected' ? false : 60_000,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    }
  )
  const metrics = (metricsData as any)?.performance || metricsData

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

  const navItems = [
    { path: '/', icon: BarChart3, label: 'Dashboard' },
    { path: '/positions', icon: TrendingUp, label: 'Positions' },
    { path: '/strategies', icon: Activity, label: 'Strategies' },
    { path: '/settings', icon: Settings, label: 'Settings' },
  ]

  return (
    <div className="min-h-screen bg-slate-900 text-white">
      {/* Main Content Container */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {/* Top Navigation Tabs */}
        <nav className="mb-6">
          <div className="flex items-center justify-center gap-1">
            {navItems.map((item) => {
              const Icon = item.icon
              const isActive = location.pathname === item.path
              return (
                <Link
                  key={item.path}
                  to={item.path}
                  className={`flex items-center gap-2 px-4 py-2.5 rounded-t-lg text-sm font-medium transition-all ${
                    isActive
                      ? 'bg-slate-800 text-primary-400 border-t border-x border-slate-700 -mb-px'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
                  }`}
                >
                  <Icon className="w-4 h-4" />
                  <span className="hidden sm:inline">{item.label}</span>
                </Link>
              )
            })}
          </div>
        </nav>

        {/* Persistent Header: Connection Status, Account Selector, Metrics, and Notifications */}
        <div className="mb-6 space-y-3">
          {/* Connection Status - Upper Left */}
          <div className="flex items-start gap-4">
            <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs ${connectionBadge.container}`}>
              <div className={`w-1.5 h-1.5 rounded-full ${connectionBadge.dot}`} />
              <span className="font-medium">{connectionBadge.label}</span>
              {socketError && (
                <span className="text-xs truncate max-w-[150px]">
                  {socketError}
                </span>
              )}
              {showRetryButton && (
                <button
                  onClick={reconnectSocket}
                  className="ml-2 text-xs font-semibold underline"
                >
                  Retry
                </button>
              )}
            </div>
          </div>

          {/* Account Selector and Metrics Card Row */}
          <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4">
            <div className="w-full sm:w-auto sm:max-w-md">
              <AccountSelector
                accounts={accounts}
                selectedAccount={selectedAccount}
                onAccountChange={setSelectedAccount}
              />
            </div>
            {metrics && (
              <div className="w-full sm:w-auto sm:flex-1 sm:max-w-md">
                <MetricsCard metrics={metrics} />
              </div>
            )}
            <div className="w-full sm:w-auto sm:ml-auto">
              <NotificationsFeed />
            </div>
          </div>
        </div>

        {/* Page Content */}
        {children}
      </main>
    </div>
  )
}

