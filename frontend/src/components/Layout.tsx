import { ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Activity, TrendingUp, Settings, BarChart3 } from 'lucide-react'
import { useAccount } from '../contexts/AccountContext'
import AccountSelector from './AccountSelector'
import NotificationsFeed from './NotificationsFeed'

interface LayoutProps {
  children: ReactNode
}

export default function Layout({ children }: LayoutProps) {
  const location = useLocation()
  const { accounts, selectedAccount, setSelectedAccount } = useAccount()

  const navItems = [
    { path: '/', icon: BarChart3, label: 'Dashboard' },
    { path: '/positions', icon: TrendingUp, label: 'Positions' },
    { path: '/strategies', icon: Activity, label: 'Strategies' },
    { path: '/settings', icon: Settings, label: 'Settings' },
  ]

  return (
    <div className="min-h-screen bg-slate-900 text-white">
      {/* Main Content Container */}
      <main className="max-w-7xl mx-auto px-2 sm:px-4 lg:px-6 xl:px-8 py-4 sm:py-6 overflow-x-hidden">
        {/* Account Selector and Notifications - Above Navigation */}
        <div className="mb-6 flex flex-col sm:flex-row items-start sm:items-center gap-4">
          <div className="w-full sm:w-auto sm:max-w-md">
            <AccountSelector
              accounts={accounts}
              selectedAccount={selectedAccount}
              onAccountChange={setSelectedAccount}
            />
          </div>
          <div className="w-full sm:w-auto sm:flex-1">
            <NotificationsFeed />
          </div>
        </div>

        {/* Navigation Tabs and Page Content Wrapper */}
        <div className="bg-slate-800/30 border border-slate-700/50 rounded-2xl p-3 sm:p-4 lg:p-6 xl:p-8 backdrop-blur-sm space-y-4 sm:space-y-5 overflow-x-hidden">
          {/* Top Navigation Tabs */}
          <nav className="overflow-x-auto -mx-3 sm:mx-0 px-3 sm:px-0">
            <div className="flex items-center justify-center gap-1 min-w-max sm:min-w-0">
              {navItems.map((item) => {
                const Icon = item.icon
                const isActive = location.pathname === item.path
                return (
                  <Link
                    key={item.path}
                    to={item.path}
                    className={`flex items-center gap-1 sm:gap-2 px-2 sm:px-4 py-2 sm:py-2.5 rounded-t-lg text-xs sm:text-sm font-medium transition-all whitespace-nowrap shrink-0 ${
                      isActive
                        ? 'bg-slate-800 text-primary-400 border-t border-x border-slate-700 -mb-px'
                        : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
                    }`}
                  >
                    <Icon className="w-3 h-3 sm:w-4 sm:h-4" />
                    <span className="hidden xs:inline">{item.label}</span>
                  </Link>
                )
              })}
            </div>
          </nav>

          {/* Page Content */}
          {children}
        </div>
      </main>
    </div>
  )
}

