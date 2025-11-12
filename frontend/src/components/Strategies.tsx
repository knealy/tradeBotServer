import { useQuery, useMutation, useQueryClient } from 'react-query'
import { useState } from 'react'
import { strategyApi } from '../services/api'
import { useAccount } from '../contexts/AccountContext'
import type { Strategy } from '../types'
import { AlertCircle, X, ChevronDown, ChevronUp, BarChart3, FileText, Play, Loader2 } from 'lucide-react'

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
  const [expandedStrategy, setExpandedStrategy] = useState<string | null>(null)
  const [selectedStrategyForStats, setSelectedStrategyForStats] = useState<string | null>(null)
  const [selectedStrategyForLogs, setSelectedStrategyForLogs] = useState<string | null>(null)

  const startMutation = useMutation(
    ({ name, symbols }: { name: string; symbols?: string[] }) =>
      strategyApi.startStrategy(name, symbols, selectedAccount?.id),
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
    ({ name }: { name: string }) => strategyApi.stopStrategy(name, selectedAccount?.id),
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
    const strategy = strategies?.find((s) => s.name === strategyName)
    const symbols = strategy?.symbols ?? []

    const normalizedStatus = (currentStatus || '').toLowerCase()
    const isEnabled = strategy?.enabled ?? ['running', 'active', 'enabled', 'started'].includes(normalizedStatus)

    if (isEnabled) {
      stopMutation.mutate({ name: strategyName })
    } else {
      startMutation.mutate({ name: strategyName, symbols })
    }
  }

  // Strategy stats query
  const { data: strategyStats, isLoading: statsLoading } = useQuery(
    ['strategy-stats', selectedStrategyForStats, selectedAccount?.id],
    () => strategyApi.getStrategyStats(selectedStrategyForStats!, selectedAccount?.id),
    {
      enabled: !!selectedStrategyForStats && !!selectedAccount,
      staleTime: 30_000,
    }
  )

  // Strategy logs query
  const { data: strategyLogs, isLoading: logsLoading } = useQuery(
    ['strategy-logs', selectedStrategyForLogs],
    () => strategyApi.getStrategyLogs(selectedStrategyForLogs!, 50),
    {
      enabled: !!selectedStrategyForLogs,
      staleTime: 10_000,
      refetchInterval: 30_000, // Refresh logs every 30 seconds
    }
  )

  // Test strategy mutation
  const testStrategyMutation = useMutation(
    ({ name }: { name: string }) => strategyApi.testStrategy(name, selectedAccount?.id),
    {
      onSuccess: () => {
        queryClient.invalidateQueries(['strategies'])
      },
    }
  )

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
                    {(() => {
                      const normalizedStatus = (strategy.status || '').toLowerCase()
                      const isRunning = strategy.is_running ?? ['running', 'active', 'started'].includes(normalizedStatus)
                      const isEnabled = strategy.enabled ?? ['running', 'active', 'enabled', 'started'].includes(normalizedStatus)
                      const badgeClasses = isRunning
                        ? 'bg-green-500/20 text-green-400'
                        : isEnabled
                        ? 'bg-blue-500/20 text-blue-400'
                        : 'bg-slate-600/50 text-slate-400'
                      const label = isRunning ? 'running' : isEnabled ? 'enabled' : normalizedStatus || 'disabled'
                      return (
                        <span className={`px-2 py-1 rounded text-xs font-semibold ${badgeClasses}`}>
                          {label}
                        </span>
                      )
                    })()}
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
                  {(() => {
                    const normalizedStatus = (strategy.status || '').toLowerCase()
                    const isEnabled = strategy.enabled ?? ['running', 'active', 'enabled', 'started'].includes(normalizedStatus)
                    const buttonLabel = startMutation.isLoading || stopMutation.isLoading
                      ? 'Loading...'
                      : isEnabled
                      ? 'Disable'
                      : 'Enable'
                    const buttonClasses = isEnabled
                      ? 'bg-red-600 hover:bg-red-500 text-white'
                      : 'bg-green-600 hover:bg-green-500 text-white'

                    return (
                      <>
                        <button
                          onClick={() => handleToggleStrategy(strategy.name, strategy.status)}
                          disabled={startMutation.isLoading || stopMutation.isLoading || !selectedAccount}
                          className={`px-4 py-2 rounded-lg font-semibold text-sm transition-colors ${buttonClasses} disabled:opacity-50 disabled:cursor-not-allowed`}
                        >
                          {buttonLabel}
                        </button>
                        <button
                          onClick={() => setExpandedStrategy(expandedStrategy === strategy.name ? null : strategy.name)}
                          className="px-4 py-2 rounded-lg font-semibold text-sm transition-colors bg-slate-700 hover:bg-slate-600 text-white flex items-center justify-center gap-2"
                        >
                          {expandedStrategy === strategy.name ? (
                            <>
                              <ChevronUp className="w-4 h-4" /> Hide Details
                            </>
                          ) : (
                            <>
                              <ChevronDown className="w-4 h-4" /> Show Details
                            </>
                          )}
                        </button>
                      </>
                    )
                  })()}
                </div>

                {/* Expanded Details Section */}
                {expandedStrategy === strategy.name && (
                  <div className="mt-4 pt-4 border-t border-slate-700 space-y-4">
                    {/* Action Buttons */}
                    <div className="flex flex-wrap gap-2">
                      <button
                        onClick={() => {
                          setSelectedStrategyForStats(strategy.name)
                          if (selectedStrategyForStats !== strategy.name) {
                            setSelectedStrategyForLogs(null)
                          }
                        }}
                        className={`px-3 py-2 rounded text-sm font-medium transition-colors flex items-center gap-2 ${
                          selectedStrategyForStats === strategy.name
                            ? 'bg-blue-500/20 text-blue-300 border border-blue-500/30'
                            : 'bg-slate-700 hover:bg-slate-600 text-slate-200'
                        }`}
                      >
                        <BarChart3 className="w-4 h-4" />
                        Stats
                      </button>
                      <button
                        onClick={() => {
                          setSelectedStrategyForLogs(strategy.name)
                          if (selectedStrategyForLogs !== strategy.name) {
                            setSelectedStrategyForStats(null)
                          }
                        }}
                        className={`px-3 py-2 rounded text-sm font-medium transition-colors flex items-center gap-2 ${
                          selectedStrategyForLogs === strategy.name
                            ? 'bg-blue-500/20 text-blue-300 border border-blue-500/30'
                            : 'bg-slate-700 hover:bg-slate-600 text-slate-200'
                        }`}
                      >
                        <FileText className="w-4 h-4" />
                        Logs
                      </button>
                      <button
                        onClick={() => testStrategyMutation.mutate({ name: strategy.name })}
                        disabled={testStrategyMutation.isLoading || !selectedAccount}
                        className="px-3 py-2 rounded text-sm font-medium transition-colors bg-purple-500/20 hover:bg-purple-500/30 text-purple-300 flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {testStrategyMutation.isLoading ? (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <Play className="w-4 h-4" />
                        )}
                        Test
                      </button>
                    </div>

                    {/* Stats Display */}
                    {selectedStrategyForStats === strategy.name && (
                      <div className="bg-slate-900/50 rounded-lg p-4">
                        {statsLoading ? (
                          <div className="text-center py-4 text-slate-400">Loading stats...</div>
                        ) : strategyStats && !strategyStats.error ? (
                          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                            <div>
                              <p className="text-xs text-slate-500 mb-1">Total Trades</p>
                              <p className="text-lg font-semibold">{strategyStats.total_trades || 0}</p>
                            </div>
                            <div>
                              <p className="text-xs text-slate-500 mb-1">Win Rate</p>
                              <p className="text-lg font-semibold">{strategyStats.win_rate?.toFixed(1) || 0}%</p>
                            </div>
                            <div>
                              <p className="text-xs text-slate-500 mb-1">Total P&L</p>
                              <p className={`text-lg font-semibold ${(strategyStats.total_pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                ${strategyStats.total_pnl?.toFixed(2) || '0.00'}
                              </p>
                            </div>
                            <div>
                              <p className="text-xs text-slate-500 mb-1">Profit Factor</p>
                              <p className="text-lg font-semibold">{strategyStats.profit_factor?.toFixed(2) || '0.00'}</p>
                            </div>
                            <div>
                              <p className="text-xs text-slate-500 mb-1">Winning Trades</p>
                              <p className="text-lg font-semibold text-green-400">{strategyStats.winning_trades || 0}</p>
                            </div>
                            <div>
                              <p className="text-xs text-slate-500 mb-1">Losing Trades</p>
                              <p className="text-lg font-semibold text-red-400">{strategyStats.losing_trades || 0}</p>
                            </div>
                            <div>
                              <p className="text-xs text-slate-500 mb-1">Best Trade</p>
                              <p className="text-lg font-semibold text-green-400">${strategyStats.best_trade?.toFixed(2) || '0.00'}</p>
                            </div>
                            <div>
                              <p className="text-xs text-slate-500 mb-1">Worst Trade</p>
                              <p className="text-lg font-semibold text-red-400">${strategyStats.worst_trade?.toFixed(2) || '0.00'}</p>
                            </div>
                          </div>
                        ) : (
                          <div className="text-center py-4 text-slate-400">
                            {strategyStats?.error || 'No stats available'}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Logs Display */}
                    {selectedStrategyForLogs === strategy.name && (
                      <div className="bg-slate-900/50 rounded-lg p-4 max-h-96 overflow-y-auto">
                        {logsLoading ? (
                          <div className="text-center py-4 text-slate-400">Loading logs...</div>
                        ) : strategyLogs?.logs && strategyLogs.logs.length > 0 ? (
                          <div className="space-y-2 font-mono text-xs">
                            {strategyLogs.logs.map((log: any, idx: number) => (
                              <div
                                key={idx}
                                className={`p-2 rounded border-l-2 ${
                                  log.level === 'ERROR' || log.level === 'CRITICAL'
                                    ? 'border-red-500 bg-red-500/10'
                                    : log.level === 'WARNING'
                                    ? 'border-yellow-500 bg-yellow-500/10'
                                    : 'border-slate-600 bg-slate-800/50'
                                }`}
                              >
                                <div className="flex items-center gap-2 mb-1">
                                  <span className="text-slate-400">{log.timestamp}</span>
                                  <span className={`px-1.5 py-0.5 rounded text-xs ${
                                    log.level === 'ERROR' || log.level === 'CRITICAL'
                                      ? 'bg-red-500/20 text-red-300'
                                      : log.level === 'WARNING'
                                      ? 'bg-yellow-500/20 text-yellow-300'
                                      : 'bg-slate-700 text-slate-300'
                                  }`}>
                                    {log.level}
                                  </span>
                                </div>
                                <p className="text-slate-200 break-words">{log.message}</p>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="text-center py-4 text-slate-400">No logs available</div>
                        )}
                      </div>
                    )}
                  </div>
                )}
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

