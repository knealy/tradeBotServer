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

  const switchMutation = useMutation(
    (accountId: string) => accountApi.switchAccount(accountId),
    {
      onSuccess: (data) => {
        if (data.success && data.account) {
          onAccountChange(data.account)
          
          // Invalidate all queries to fetch data for new account
          queryClient.invalidateQueries(['accounts'])
          queryClient.invalidateQueries(['accountInfo'])
          queryClient.invalidateQueries(['positions'])
          queryClient.invalidateQueries(['orders'])
          queryClient.invalidateQueries(['metrics'])
          queryClient.invalidateQueries(['trades'])
          queryClient.invalidateQueries(['performance'])
          
          setIsOpen(false)
        }
      },
      onError: (error) => {
        console.error('Failed to switch account:', error)
      }
    }
  )

  const handleAccountSelect = async (account: Account) => {
    if (account.accountId === selectedAccount?.accountId) {
      setIsOpen(false)
      return
    }
    
    switchMutation.mutate(account.accountId)
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
                <div className="font-medium">{selectedAccount.accountId}</div>
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
                const isSelected = account.accountId === selectedAccount?.accountId
                const isLoading = switchMutation.isLoading && 
                                 switchMutation.variables === account.accountId
                
                return (
                  <button
                    key={account.accountId}
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
                        <div className="font-medium">{account.accountId}</div>
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

