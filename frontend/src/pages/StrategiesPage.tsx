import { useQuery, useMutation, useQueryClient } from 'react-query'
import { useAccount } from '../contexts/AccountContext'
import { strategyApi } from '../services/api'
import AccountSelector from '../components/AccountSelector'

export default function StrategiesPage() {
  const { accounts, selectedAccount, setSelectedAccount } = useAccount()
  const queryClient = useQueryClient()
  
  const { data: strategies = [], isLoading } = useQuery(
    'strategies',
    strategyApi.getStrategies,
    {
      refetchInterval: 10000,
    }
  )

  const startMutation = useMutation(
    (strategyName: string) => strategyApi.startStrategy(strategyName),
    {
      onSuccess: (data, strategyName) => {
        queryClient.invalidateQueries(['strategies'])
        if (data.success) {
          console.log(`✅ Started strategy: ${strategyName}`)
        } else {
          console.error(`❌ Failed to start strategy: ${data.message}`)
          alert(`Failed to start strategy: ${data.message}`)
        }
      },
      onError: (error: any) => {
        console.error('Error starting strategy:', error)
        alert(`Error starting strategy: ${error.message || 'Unknown error'}`)
      }
    }
  )

  const stopMutation = useMutation(
    (strategyName: string) => strategyApi.stopStrategy(strategyName),
    {
      onSuccess: (data, strategyName) => {
        queryClient.invalidateQueries(['strategies'])
        if (data.success) {
          console.log(`✅ Stopped strategy: ${strategyName}`)
        } else {
          console.error(`❌ Failed to stop strategy: ${data.message}`)
          alert(`Failed to stop strategy: ${data.message}`)
        }
      },
      onError: (error: any) => {
        console.error('Error stopping strategy:', error)
        alert(`Error stopping strategy: ${error.message || 'Unknown error'}`)
      }
    }
  )

  const handleToggleStrategy = (strategyName: string, currentStatus: string) => {
    if (currentStatus === 'running') {
      stopMutation.mutate(strategyName)
    } else {
      startMutation.mutate(strategyName)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Strategies</h1>
          <p className="text-slate-400 mt-2">Manage your trading strategies</p>
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

      {isLoading ? (
        <div className="text-center py-8">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary-500 mx-auto"></div>
        </div>
      ) : (
        <div className="grid gap-4">
          {strategies.length === 0 ? (
            <div className="bg-slate-800 rounded-lg p-8 text-center">
              <p className="text-slate-400">No strategies configured</p>
            </div>
          ) : (
            strategies.map((strategy: any) => (
              <div
                key={strategy.name}
                className="bg-slate-800 rounded-lg p-6 border border-slate-700"
              >
                <div className="flex items-center justify-between">
                  <div className="flex-1">
                    <h3 className="text-xl font-semibold">{strategy.name}</h3>
                    <p className="text-slate-400 text-sm mt-1">
                      Symbols: {strategy.symbols?.join(', ') || 'N/A'}
                    </p>
                  </div>
                  <div className="flex items-center gap-4">
                    <span
                      className={`px-3 py-1 rounded-full text-sm font-medium ${
                        strategy.status === 'running'
                          ? 'bg-green-900 text-green-200'
                          : strategy.status === 'stopped'
                          ? 'bg-slate-700 text-slate-300'
                          : 'bg-red-900 text-red-200'
                      }`}
                    >
                      {strategy.status === 'running' ? 'active' : strategy.status}
                    </span>
                    <button
                      onClick={() => handleToggleStrategy(strategy.name, strategy.status)}
                      disabled={startMutation.isLoading || stopMutation.isLoading || !selectedAccount}
                      className={`px-4 py-2 rounded-lg font-semibold text-sm transition-colors ${
                        strategy.status === 'running'
                          ? 'bg-slate-600 hover:bg-slate-500 text-white'
                          : 'bg-green-600 hover:bg-green-500 text-white'
                      } disabled:opacity-50 disabled:cursor-not-allowed`}
                    >
                      {startMutation.isLoading || stopMutation.isLoading
                        ? 'Loading...'
                        : strategy.status === 'running'
                        ? 'Disabled'
                        : 'Enabled'}
                    </button>
                  </div>
                </div>
                
                {strategy.config && (
                  <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                    {Object.entries(strategy.config).slice(0, 4).map(([key, value]: [string, any]) => (
                      <div key={key}>
                        <span className="text-slate-400">{key}:</span>
                        <span className="ml-2 text-white">
                          {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}

