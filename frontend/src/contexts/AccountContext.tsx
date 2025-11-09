import { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import { useQuery } from 'react-query'
import { accountApi } from '../services/api'
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

  // Auto-select first account on mount
  useEffect(() => {
    if (accounts.length > 0 && !selectedAccount) {
      setSelectedAccount(accounts[0])
    }
  }, [accounts, selectedAccount])

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

