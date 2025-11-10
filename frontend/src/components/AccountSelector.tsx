import { useState } from 'react'
import { useMutation, useQueryClient } from 'react-query'
import { accountApi } from '../services/api'
import { Account } from '../types'
import { ChevronDown, Check, AlertCircle } from 'lucide-react'

interface AccountSelectorProps {
  accounts: Account[]
  selectedAccount: Account | null
  onAccountChange: (account: Account) => void
}

export default function AccountSelector({ 
  accounts, 
  selectedAccount, 
  onAccountChange 
}: AccountSelectorProps) {
  const [isOpen, setIsOpen] = useState(false)
  const queryClient = useQueryClient()

  const getAccountIdentifier = (account: Account) =>
    account.id || account.accountId || account.account_id || account.name || 'unknown'

  const getAccountLabel = (account: Account) =>
    account.name || account.accountId || account.account_id || getAccountIdentifier(account)

  const switchMutation = useMutation(
    (accountId: string) => accountApi.switchAccount(accountId),
    {
      onSuccess: (data) => {
        if (data.success && data.account) {
          const normalized: Account = {
            id: data.account.id || data.account.accountId || data.account.account_id || getAccountIdentifier(data.account),
            name: data.account.name || data.account.accountId || getAccountIdentifier(data.account),
            status: data.account.status || 'active',
            balance: Number(data.account.balance ?? 0),
            currency: data.account.currency || 'USD',
            account_type: data.account.account_type || 'unknown',
            accountId: data.account.accountId,
            account_id: data.account.account_id,
            equity: data.account.equity ?? data.account.balance ?? 0,
            dailyPnL: data.account.dailyPnL ?? data.account.daily_pnl ?? 0,
            daily_pnl: data.account.dailyPnL ?? data.account.daily_pnl ?? 0,
          }

          onAccountChange(normalized)

          // Optimistically update account info cache so UI reflects immediately
          queryClient.setQueryData<Account>('accountInfo', normalized)

          // Update accounts list balances instantly
          queryClient.setQueryData<Account[]>(
            'accounts',
            (old) =>
              old?.map((acct) =>
                getAccountIdentifier(acct) === normalized.id
                  ? { ...acct, balance: normalized.balance, status: normalized.status }
                  : acct
              ) || []
          )

          // Trigger refetch for other data sources
          const invalidationTargets = [
            'accounts',
            'accountInfo',
            'positions',
            'orders',
            'metrics',
            'trades',
            'performance',
            'performanceHistory',
            'historicalData',
          ]
          invalidationTargets.forEach((target) =>
            queryClient.invalidateQueries({
              predicate: (query) => query.queryKey[0] === target,
            })
          )

          setIsOpen(false)
        }
      },
      onError: (error) => {
        console.error('Failed to switch account:', error)
      }
    }
  )

  const handleAccountSelect = async (account: Account) => {
    const selectedId = selectedAccount ? getAccountIdentifier(selectedAccount) : null
    const accountId = getAccountIdentifier(account)

    if (accountId === selectedId) {
      setIsOpen(false)
      return
    }
    
    switchMutation.mutate(accountId)
  }

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full bg-slate-700 hover:bg-slate-600 rounded-lg px-4 py-3 flex items-center justify-between transition-colors border border-slate-600"
        disabled={switchMutation.isLoading}
      >
        <div className="flex items-center gap-3">
          <div className="w-2 h-2 rounded-full bg-green-500"></div>
          <div className="text-left">
            {selectedAccount ? (
              <>
                <div className="font-medium">{getAccountLabel(selectedAccount)}</div>
                <div className="text-xs text-slate-400">
                  ${selectedAccount.balance?.toLocaleString() || 'N/A'}
                </div>
              </>
            ) : (
              <div className="text-slate-400">Select Account</div>
            )}
          </div>
        </div>
        <ChevronDown 
          className={`w-5 h-5 transition-transform ${isOpen ? 'rotate-180' : ''}`} 
        />
      </button>

      {isOpen && (
        <>
          {/* Backdrop */}
          <div 
            className="fixed inset-0 z-10" 
            onClick={() => setIsOpen(false)}
          />
          
          {/* Dropdown */}
          <div className="absolute top-full mt-2 w-full bg-slate-700 border border-slate-600 rounded-lg shadow-xl z-50 max-h-96 overflow-y-auto">
            {accounts.length === 0 ? (
              <div className="px-4 py-3 text-slate-400 text-sm">
                No accounts available
              </div>
            ) : (
              accounts.map((account) => {
                const accountId = getAccountIdentifier(account)
                const selectedId = selectedAccount ? getAccountIdentifier(selectedAccount) : null
                const isSelected = accountId === selectedId
                const isLoading = switchMutation.isLoading && 
                                 switchMutation.variables === accountId
                
                return (
                  <button
                    key={accountId}
                    onClick={() => handleAccountSelect(account)}
                    disabled={switchMutation.isLoading}
                    className={`w-full px-4 py-3 flex items-center justify-between hover:bg-slate-600 transition-colors ${
                      isSelected ? 'bg-slate-600' : ''
                    } ${switchMutation.isLoading ? 'opacity-50' : ''}`}
                  >
                    <div className="flex items-center gap-3">
                      <div className={`w-2 h-2 rounded-full ${
                        account.status === 'active' ? 'bg-green-500' : 'bg-slate-500'
                      }`}></div>
                      <div className="text-left">
                        <div className="font-medium">{getAccountLabel(account)}</div>
                        <div className="text-xs text-slate-400">
                          ${account.balance?.toLocaleString() || 'N/A'}
                          {account.status && (
                            <span className="ml-2 capitalize">• {account.status}</span>
                          )}
                        </div>
                      </div>
                    </div>
                    
                    <div className="flex items-center gap-2">
                      {isLoading && (
                        <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-primary-500"></div>
                      )}
                      {isSelected && !isLoading && (
                        <Check className="w-5 h-5 text-primary-400" />
                      )}
                    </div>
                  </button>
                )
              })
            )}
          </div>
        </>
      )}

      {/* Error Message */}
      {switchMutation.isError && (
        <div className="mt-2 flex items-center gap-2 text-red-400 text-sm">
          <AlertCircle className="w-4 h-4" />
          <span>Failed to switch account</span>
        </div>
      )}
      
      {/* Success Message */}
      {switchMutation.isSuccess && (
        <div className="mt-2 flex items-center gap-2 text-green-400 text-sm">
          <Check className="w-4 h-4" />
          <span>Account switched successfully</span>
        </div>
      )}
    </div>
  )
}

