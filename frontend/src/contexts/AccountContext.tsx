import { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import { useQuery } from 'react-query'
import { accountApi, settingsApi } from '../services/api'
import { Account } from '../types'

interface AccountContextType {
  accounts: Account[]
  selectedAccount: Account | null
  setSelectedAccount: (account: Account | null) => void
  isLoading: boolean
}

const AccountContext = createContext<AccountContextType | undefined>(undefined)

export function AccountProvider({ children }: { children: ReactNode }) {
  const [selectedAccount, setSelectedAccount] = useState<Account | null>(null)

  // Fetch accounts
  const { data: accounts = [], isLoading } = useQuery(
    'accounts',
    accountApi.getAccounts,
    {
      refetchInterval: 60000,
      staleTime: 30000,
    }
  )

  const { data: settingsResponse } = useQuery(
    ['settings', 'global'],
    () => settingsApi.getSettings('global'),
    {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    }
  )

  // Auto-select first account on mount
  useEffect(() => {
    if (accounts.length > 0 && !selectedAccount) {
      const preferredId = settingsResponse?.settings?.defaultAccount
      const preferred = preferredId
        ? accounts.find(
            (account) =>
              String(account.id) === String(preferredId) ||
              String(account.account_id) === String(preferredId) ||
              String(account.accountId) === String(preferredId)
          )
        : null
      setSelectedAccount(preferred || accounts[0])
    }
  }, [accounts, selectedAccount, settingsResponse])

  return (
    <AccountContext.Provider
      value={{
        accounts,
        selectedAccount,
        setSelectedAccount,
        isLoading,
      }}
    >
      {children}
    </AccountContext.Provider>
  )
}

export function useAccount() {
  const context = useContext(AccountContext)
  if (context === undefined) {
    throw new Error('useAccount must be used within an AccountProvider')
  }
  return context
}

