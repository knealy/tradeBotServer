import { ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Activity, TrendingUp, Settings, BarChart3 } from 'lucide-react'

interface LayoutProps {
  children: ReactNode
}

export default function Layout({ children }: LayoutProps) {
  const location = useLocation()

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

        {/* Page Content */}
        {children}
      </main>
    </div>
  )
}

