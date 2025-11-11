import { useQuery, useMutation, useQueryClient } from 'react-query'
import { useState } from 'react'
import { strategyApi } from '../services/api'
import { useAccount } from '../contexts/AccountContext'
import type { Strategy } from '../types'
import { AlertCircle, X } from 'lucide-react'

export default function Strategies() {
  const { selectedAccount } = useAccount()
  const queryClient = useQueryClient()

  const { data: strategies, isLoading } = useQuery<Strategy[]>(
    ['strategies'],
    strategyApi.getStrategies,
    {
      staleTime: 30_000,
      refetchInterval: 60_000,
    }
  )

  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const startMutation = useMutation(
    (strategyName: string) => strategyApi.startStrategy(strategyName, undefined, selectedAccount?.id),
    {
      onSuccess: (data, strategyName) => {
        queryClient.invalidateQueries(['strategies'])
        setErrorMessage(null)
        if (data.success) {
          console.log(`✅ Started strategy: ${strategyName}`)
        } else {
          const error = data.error || data.message || 'Unknown error'
          setErrorMessage(`Failed to start ${strategyName}: ${error}`)
          console.error(`❌ Failed to start strategy: ${error}`)
        }
      },
      onError: (error: any) => {
        console.error('❌ Strategy start error (full):', error)
        console.error('❌ Error response:', error?.response)
        console.error('❌ Error response data:', error?.response?.data)
        
        // Try multiple ways to extract the error message
        let errorMsg = 'Unknown error'
        if (error?.response?.data) {
          errorMsg = error.response.data.error || 
                    error.response.data.message || 
                    JSON.stringify(error.response.data) ||
                    'Unknown error'
        } else if (error?.message) {
          errorMsg = error.message
        }
        
        setErrorMessage(`Failed to start strategy: ${errorMsg}`)
      },
    }
  )

  const stopMutation = useMutation(
    (strategyName: string) => strategyApi.stopStrategy(strategyName, selectedAccount?.id),
    {
      onSuccess: (data, strategyName) => {
        queryClient.invalidateQueries(['strategies'])
        setErrorMessage(null)
        if (data.success) {
          console.log(`✅ Stopped strategy: ${strategyName}`)
        } else {
          const error = data.error || data.message || 'Unknown error'
          setErrorMessage(`Failed to stop ${strategyName}: ${error}`)
          console.error(`❌ Failed to stop strategy: ${error}`)
        }
      },
      onError: (error: any) => {
        console.error('❌ Strategy stop error (full):', error)
        console.error('❌ Error response:', error?.response)
        console.error('❌ Error response data:', error?.response?.data)
        
        // Try multiple ways to extract the error message
        let errorMsg = 'Unknown error'
        if (error?.response?.data) {
          errorMsg = error.response.data.error || 
                    error.response.data.message || 
                    JSON.stringify(error.response.data) ||
                    'Unknown error'
        } else if (error?.message) {
          errorMsg = error.message
        }
        
        setErrorMessage(`Failed to stop strategy: ${errorMsg}`)
      },
    }
  )

  const handleToggleStrategy = (strategyName: string, currentStatus: string) => {
    if (currentStatus === 'running') {
      stopMutation.mutate(strategyName)
    } else {
      startMutation.mutate(strategyName)
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-slate-400">Loading strategies...</div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold mb-2">Strategies</h1>
        <p className="text-slate-400">Manage your trading strategies</p>
      </div>

      {errorMessage && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-5 h-5 text-red-400" />
            <p className="text-red-400 text-sm">{errorMessage}</p>
          </div>
          <button
            onClick={() => setErrorMessage(null)}
            className="text-red-400 hover:text-red-300"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {!selectedAccount && (
        <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-4">
          <p className="text-yellow-500 text-sm">
            ⚠️ Please select an account to manage strategies
          </p>
        </div>
      )}

      <div className="space-y-4">
        {strategies && strategies.length > 0 ? (
          strategies.map((strategy) => (
            <div
              key={strategy.name}
              className="bg-slate-800 rounded-lg p-6 border border-slate-700 hover:border-slate-600 transition-colors"
            >
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-3 mb-2">
                    <h3 className="text-lg font-semibold">{strategy.name}</h3>
                    <span
                      className={`px-2 py-1 rounded text-xs font-semibold ${
                        strategy.status === 'running'
                          ? 'bg-green-500/20 text-green-400'
                          : strategy.status === 'stopped'
                          ? 'bg-slate-600/50 text-slate-400'
                          : 'bg-red-500/20 text-red-400'
                      }`}
                    >
                      {strategy.status === 'running' ? 'active' : strategy.status}
                    </span>
                  </div>
                  
                  <div className="text-sm text-slate-400 space-y-1">
                    <p>
                      <span className="font-medium">Symbols:</span>{' '}
                      {strategy.symbols && strategy.symbols.length > 0
                        ? strategy.symbols.join(', ')
                        : 'N/A'}
                    </p>
                    {strategy.description && (
                      <p className="mt-2">{strategy.description}</p>
                    )}
                  </div>

                  {strategy.stats && (
                    <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                      <div className="bg-slate-900/50 rounded p-2">
                        <p className="text-slate-500">Trades</p>
                        <p className="font-semibold">{strategy.stats.total_trades || 0}</p>
                      </div>
                      <div className="bg-slate-900/50 rounded p-2">
                        <p className="text-slate-500">Win Rate</p>
                        <p className="font-semibold">
                          {strategy.stats.win_rate ? `${strategy.stats.win_rate.toFixed(1)}%` : 'N/A'}
                        </p>
                      </div>
                      <div className="bg-slate-900/50 rounded p-2">
                        <p className="text-slate-500">Total P&L</p>
                        <p
                          className={`font-semibold ${
                            (strategy.stats.total_pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'
                          }`}
                        >
                          {strategy.stats.total_pnl ? `$${strategy.stats.total_pnl.toFixed(2)}` : '$0.00'}
                        </p>
                      </div>
                      <div className="bg-slate-900/50 rounded p-2">
                        <p className="text-slate-500">Avg P&L</p>
                        <p
                          className={`font-semibold ${
                            (strategy.stats.avg_pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'
                          }`}
                        >
                          {strategy.stats.avg_pnl ? `$${strategy.stats.avg_pnl.toFixed(2)}` : '$0.00'}
                        </p>
                      </div>
                    </div>
                  )}
                </div>

                <div className="flex flex-col gap-2">
                  <button
                    onClick={() => handleToggleStrategy(strategy.name, strategy.status)}
                    disabled={startMutation.isLoading || stopMutation.isLoading || !selectedAccount}
                    className={`px-4 py-2 rounded-lg font-semibold text-sm transition-colors ${
                      strategy.status === 'running' || strategy.status === 'active'
                        ? 'bg-red-600 hover:bg-red-500 text-white'
                        : 'bg-green-600 hover:bg-green-500 text-white'
                    } disabled:opacity-50 disabled:cursor-not-allowed`}
                  >
                    {startMutation.isLoading || stopMutation.isLoading
                      ? 'Loading...'
                      : strategy.status === 'running' || strategy.status === 'active'
                      ? 'Disable'
                      : 'Enable'}
                  </button>
                </div>
              </div>
            </div>
          ))
        ) : (
          <div className="bg-slate-800 rounded-lg p-12 border border-slate-700 text-center">
            <p className="text-slate-400">No strategies configured</p>
          </div>
        )}
      </div>
    </div>
  )
}

