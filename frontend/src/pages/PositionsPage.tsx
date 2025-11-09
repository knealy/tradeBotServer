import { useAccount } from '../contexts/AccountContext'
import AccountSelector from '../components/AccountSelector'
import PositionsOverview from '../components/PositionsOverview'

export default function PositionsPage() {
  const { accounts, selectedAccount, setSelectedAccount } = useAccount()

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Positions</h1>
          <p className="text-slate-400 mt-2">Monitor and manage your open positions</p>
        </div>
      </div>

      {/* Account Selection */}
      <div className="max-w-md">
        <AccountSelector
          accounts={accounts}
          selectedAccount={selectedAccount}
          onAccountChange={setSelectedAccount}
        />
      </div>
      
      <PositionsOverview />
    </div>
  )
}

