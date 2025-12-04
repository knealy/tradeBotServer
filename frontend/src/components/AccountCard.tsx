import { Account } from '../types'
import { TrendingUp, TrendingDown } from 'lucide-react'

interface AccountCardProps {
  account: Account
  isSelected?: boolean
  onSelect?: () => void
}

export default function AccountCard({ account, isSelected, onSelect }: AccountCardProps) {
  const pnl = account.dailyPnL ?? account.daily_pnl ?? 0
  const pnlColor = pnl >= 0 ? 'text-green-400' : 'text-red-400'
  const PnlIcon = pnl >= 0 ? TrendingUp : TrendingDown

  return (
    <div
      className={`p-5 rounded-xl border transition-all cursor-pointer backdrop-blur-sm ${
        isSelected
          ? 'bg-slate-800/60 border-slate-700/50 shadow-lg'
          : 'bg-slate-800/40 border-slate-700/30 hover:border-slate-600/50 hover:bg-slate-800/50'
      }`}
      onClick={onSelect}
    >
      <div className="flex items-center justify-between mb-4">
        <div className="flex-1 min-w-0">
          <h3 className="text-base font-semibold text-slate-100 truncate">{account.name}</h3>
          <p className="text-xs text-slate-500 mt-0.5">{account.account_type}</p>
        </div>
        <div className={`ml-3 px-2.5 py-1 rounded-md text-xs font-medium shrink-0 ${
          account.status === 'active' 
            ? 'bg-green-500/15 text-green-400 border border-green-500/20' 
            : 'bg-slate-700/50 text-slate-400 border border-slate-600/30'
        }`}>
          {account.status}
        </div>
      </div>

      <div className="space-y-3">
          <div>
          <p className="text-xs text-slate-500 mb-1">Balance</p>
          <p className="text-2xl font-bold text-slate-100 tracking-tight">
              {account.currency} {account.balance.toLocaleString('en-US', {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              })}
            </p>
        </div>

        {pnl !== 0 && (
          <div className={`flex items-center gap-2 pt-2 border-t border-slate-700/50 ${pnlColor}`}>
            <PnlIcon className="w-4 h-4 shrink-0" />
            <div className="min-w-0">
              <p className="text-xs text-slate-500">Daily P&L</p>
              <p className="text-lg font-semibold">
                {pnl >= 0 ? '+' : ''}
                {account.currency} {Math.abs(pnl).toLocaleString('en-US', {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

