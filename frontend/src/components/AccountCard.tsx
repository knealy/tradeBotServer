import { Account } from '../types'
import { DollarSign, TrendingUp, TrendingDown } from 'lucide-react'

interface AccountCardProps {
  account: Account
  isSelected?: boolean
  onSelect?: () => void
}

export default function AccountCard({ account, isSelected, onSelect }: AccountCardProps) {
  const pnl = account.balance > 0 ? account.balance : 0 // This would come from account data
  const pnlColor = pnl >= 0 ? 'text-green-400' : 'text-red-400'
  const PnlIcon = pnl >= 0 ? TrendingUp : TrendingDown

  return (
    <div
      className={`p-6 rounded-lg border-2 transition-all cursor-pointer ${
        isSelected
          ? 'bg-slate-800 border-primary-500 shadow-lg shadow-primary-500/20'
          : 'bg-slate-800/50 border-slate-700 hover:border-slate-600'
      }`}
      onClick={onSelect}
    >
      <div className="flex items-start justify-between mb-4">
        <div>
          <h3 className="text-lg font-semibold">{account.name}</h3>
          <p className="text-sm text-slate-400">{account.account_type}</p>
        </div>
        <div className={`px-2 py-1 rounded text-xs font-medium ${
          account.status === 'active' 
            ? 'bg-green-500/20 text-green-400' 
            : 'bg-slate-700 text-slate-400'
        }`}>
          {account.status}
        </div>
      </div>

      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <DollarSign className="w-5 h-5 text-slate-400" />
          <div>
            <p className="text-sm text-slate-400">Balance</p>
            <p className="text-2xl font-bold">
              {account.currency} {account.balance.toLocaleString('en-US', {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              })}
            </p>
          </div>
        </div>

        {pnl !== 0 && (
          <div className={`flex items-center gap-2 ${pnlColor}`}>
            <PnlIcon className="w-4 h-4" />
            <div>
              <p className="text-sm">P&L</p>
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

