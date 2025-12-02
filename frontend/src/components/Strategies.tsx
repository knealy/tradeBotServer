import { useQuery, useMutation, useQueryClient } from 'react-query'
import { useState } from 'react'
import { strategyApi } from '../services/api'
import { useAccount } from '../contexts/AccountContext'
import type { Strategy } from '../types'
import { AlertCircle, X, ChevronDown, ChevronUp, BarChart3, FileText, Play, Loader2, Settings, Plus, Save, CheckCircle2 } from 'lucide-react'

// Available symbols for trading
const AVAILABLE_SYMBOLS = ['MNQ', 'MES', 'MYM', 'M2K', 'MGC', 'GC']

// Strategy Insights UI - v2.0 with stats, logs, and test buttons
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
  const [selectedStrategyForVerify, setSelectedStrategyForVerify] = useState<string | null>(null)
  const [editingConfig, setEditingConfig] = useState<string | null>(null)
  const [configEdits, setConfigEdits] = useState<{
    symbols: string[]
    position_size: number
    max_positions: number
    strategy_params: Record<string, any> // Strategy-specific parameters
  }>({ 
    symbols: [], 
    position_size: 1, 
    max_positions: 2,
    strategy_params: {}
  })

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

  const { data: verificationData, isLoading: verifyLoading, refetch: refetchVerify } = useQuery(
    ['strategy-verify', selectedStrategyForVerify],
    () => strategyApi.verifyStrategy(selectedStrategyForVerify!),
    {
      enabled: !!selectedStrategyForVerify,
      staleTime: 30_000,
      refetchInterval: 60_000, // Refetch every minute to update next execution time
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

  // Update config mutation
  const updateConfigMutation = useMutation(
    ({ name, config }: { name: string; config: { symbols?: string[]; position_size?: number; max_positions?: number; strategy_params?: Record<string, any> } }) =>
      strategyApi.updateStrategyConfig(name, config),
    {
      onSuccess: (data, { name }) => {
        queryClient.invalidateQueries(['strategies'])
        setEditingConfig(null)
        setErrorMessage(null)
        if (data.success) {
          console.log(`✅ Updated config for: ${name}`)
        }
      },
      onError: (error: any) => {
        console.error('❌ Config update error:', error)
        let errorMsg = 'Unknown error'
        if (error?.response?.data) {
          errorMsg = error.response.data.error || error.response.data.message || JSON.stringify(error.response.data)
        } else if (error?.message) {
          errorMsg = error.message
        }
        setErrorMessage(`Failed to update config: ${errorMsg}`)
      },
    }
  )

  // Start editing config for a strategy
  const startEditingConfig = (strategy: Strategy) => {
    setEditingConfig(strategy.name)
    
    // Extract strategy-specific parameters from settings
    const strategyParams: Record<string, any> = {}
    if (strategy.settings) {
      // Overnight range strategy parameters
      if (strategy.name === 'overnight_range' || strategy.name.toLowerCase().includes('overnight')) {
        strategyParams.overnight_start_time = strategy.settings.overnight_start_time || '18:00'
        strategyParams.overnight_end_time = strategy.settings.overnight_end_time || '09:30'
        strategyParams.market_open_time = strategy.settings.market_open_time || '09:30'
        strategyParams.atr_period = strategy.settings.atr_period || 14
        strategyParams.atr_timeframe = strategy.settings.atr_timeframe || '5m'
        strategyParams.stop_atr_multiplier = strategy.settings.stop_atr_multiplier || 1.25
        strategyParams.tp_atr_multiplier = strategy.settings.tp_atr_multiplier || 2.0
        strategyParams.breakeven_enabled = strategy.settings.breakeven_enabled !== undefined ? strategy.settings.breakeven_enabled : true
        strategyParams.breakeven_profit_points = strategy.settings.breakeven_profit_points || 15.0
        strategyParams.range_break_offset = strategy.settings.range_break_offset || 0.25
      }
    }
    
    setConfigEdits({
      symbols: strategy.symbols || [],
      position_size: strategy.settings?.position_size || 1,
      max_positions: strategy.settings?.max_positions || 2,
      strategy_params: strategyParams,
    })
  }

  // Save config changes
  const saveConfigChanges = (strategyName: string) => {
    updateConfigMutation.mutate({
      name: strategyName,
      config: {
        symbols: configEdits.symbols,
        position_size: configEdits.position_size,
        max_positions: configEdits.max_positions,
        strategy_params: configEdits.strategy_params,
      },
    })
  }

  // Add symbol to config
  const addSymbol = (symbol: string) => {
    if (!configEdits.symbols.includes(symbol)) {
      setConfigEdits({ ...configEdits, symbols: [...configEdits.symbols, symbol] })
    }
  }

  // Remove symbol from config
  const removeSymbol = (symbol: string) => {
    setConfigEdits({ ...configEdits, symbols: configEdits.symbols.filter((s) => s !== symbol) })
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-slate-400">Loading strategies...</div>
      </div>
    )
  }

  return (
    <div className="bg-slate-800/30 border border-slate-700/50 rounded-2xl p-6 sm:p-8 backdrop-blur-sm space-y-5">
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
              className="bg-slate-800/50 rounded-xl p-6 border border-slate-700/50 hover:border-slate-600/50 transition-colors"
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
                    <p>
                      <span className="font-medium">Position Size:</span>{' '}
                      {strategy.settings?.position_size || 1} contracts
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
                      <button
                        onClick={() => {
                          if (expandedStrategy !== strategy.name) {
                            setExpandedStrategy(strategy.name)
                          }
                          setSelectedStrategyForVerify(strategy.name)
                          setSelectedStrategyForLogs(null)
                          setSelectedStrategyForStats(null)
                          refetchVerify()
                        }}
                        className="px-4 py-2 rounded-lg font-semibold text-sm transition-colors bg-emerald-600 hover:bg-emerald-500 text-white flex items-center justify-center gap-2"
                      >
                        <CheckCircle2 className="w-4 h-4" />
                        Verify
                      </button>
                      <button
                        onClick={() => {
                          if (expandedStrategy !== strategy.name) {
                            setExpandedStrategy(strategy.name)
                          }
                          startEditingConfig(strategy)
                        }}
                        className="px-4 py-2 rounded-lg font-semibold text-sm transition-colors bg-orange-500 hover:bg-orange-400 text-white flex items-center justify-center gap-2"
                      >
                        <Settings className="w-4 h-4" />
                        Configure
                      </button>
                      <button
                        onClick={() => {
                          if (expandedStrategy !== strategy.name) {
                            setExpandedStrategy(strategy.name)
                          }
                          testStrategyMutation.mutate({ name: strategy.name })
                        }}
                        disabled={testStrategyMutation.isLoading || !selectedAccount}
                        className="px-4 py-2 rounded-lg font-semibold text-sm transition-colors bg-purple-600 hover:bg-purple-500 text-white flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {testStrategyMutation.isLoading ? (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <Play className="w-4 h-4" />
                        )}
                        Test
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
                            setSelectedStrategyForVerify(null)
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
                        onClick={() => {
                          setSelectedStrategyForVerify(strategy.name)
                          if (selectedStrategyForVerify !== strategy.name) {
                            setSelectedStrategyForStats(null)
                            setSelectedStrategyForLogs(null)
                          }
                        }}
                        className={`px-3 py-2 rounded text-sm font-medium transition-colors flex items-center gap-2 ${
                          selectedStrategyForVerify === strategy.name
                            ? 'bg-green-500/20 text-green-300 border border-green-500/30'
                            : 'bg-slate-700 hover:bg-slate-600 text-slate-200'
                        }`}
                      >
                        <CheckCircle2 className="w-4 h-4" />
                        Verify
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
                      <button
                        onClick={() => {
                          if (editingConfig === strategy.name) {
                            setEditingConfig(null)
                          } else {
                            startEditingConfig(strategy)
                          }
                        }}
                        className={`px-3 py-2 rounded text-sm font-medium transition-colors flex items-center gap-2 ${
                          editingConfig === strategy.name
                            ? 'bg-orange-500/20 text-orange-300 border border-orange-500/30'
                            : 'bg-slate-700 hover:bg-slate-600 text-slate-200'
                        }`}
                      >
                        <Settings className="w-4 h-4" />
                        Configure
                      </button>
                    </div>

                    {/* Configuration Editor */}
                    {editingConfig === strategy.name && (
                      <div className="bg-slate-900/50 rounded-lg p-4 space-y-4">
                        <h4 className="text-sm font-semibold text-slate-300">Strategy Configuration</h4>

                        {/* Symbols */}
                        <div>
                          <label className="block text-xs text-slate-400 mb-2">Trading Symbols</label>
                          <div className="flex flex-wrap gap-2 mb-2">
                            {configEdits.symbols.map((symbol) => (
                              <span
                                key={symbol}
                                className="px-2 py-1 bg-blue-500/20 text-blue-300 rounded text-sm flex items-center gap-1"
                              >
                                {symbol}
                                <button
                                  onClick={() => removeSymbol(symbol)}
                                  className="hover:text-red-400 transition-colors"
                                >
                                  <X className="w-3 h-3" />
                                </button>
                              </span>
                            ))}
                          </div>
                          <div className="flex flex-wrap gap-1">
                            {AVAILABLE_SYMBOLS.filter((s) => !configEdits.symbols.includes(s)).map((symbol) => (
                              <button
                                key={symbol}
                                onClick={() => addSymbol(symbol)}
                                className="px-2 py-1 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded text-xs flex items-center gap-1 transition-colors"
                              >
                                <Plus className="w-3 h-3" />
                                {symbol}
                              </button>
                            ))}
                          </div>
                        </div>

                        {/* Position Size */}
                        <div>
                          <label className="block text-xs text-slate-400 mb-2">Position Size (contracts)</label>
                          <input
                            type="number"
                            min="1"
                            max="100"
                            value={configEdits.position_size}
                            onChange={(e) =>
                              setConfigEdits({ ...configEdits, position_size: Math.max(1, parseInt(e.target.value) || 1) })
                            }
                            className="w-24 px-3 py-2 bg-slate-800 border border-slate-600 rounded text-sm text-white focus:outline-none focus:border-blue-500"
                          />
                        </div>

                        {/* Max Positions */}
                        <div>
                          <label className="block text-xs text-slate-400 mb-2">Max Concurrent Positions</label>
                          <input
                            type="number"
                            min="1"
                            max="10"
                            value={configEdits.max_positions}
                            onChange={(e) =>
                              setConfigEdits({ ...configEdits, max_positions: Math.max(1, parseInt(e.target.value) || 1) })
                            }
                            className="w-24 px-3 py-2 bg-slate-800 border border-slate-600 rounded text-sm text-white focus:outline-none focus:border-blue-500"
                          />
                        </div>

                        {/* Strategy-Specific Parameters */}
                        {(strategy.name === 'overnight_range' || strategy.name.toLowerCase().includes('overnight')) && (
                          <div className="border-t border-slate-700 pt-4 space-y-3">
                            <h5 className="text-xs font-semibold text-slate-300 mb-2">Overnight Range Strategy Parameters</h5>
                            
                            {/* Overnight Time Range */}
                            <div className="grid grid-cols-2 gap-3">
                              <div>
                                <label className="block text-xs text-slate-400 mb-1">Overnight Start Time</label>
                                <input
                                  type="text"
                                  placeholder="18:00"
                                  value={configEdits.strategy_params?.overnight_start_time || '18:00'}
                                  onChange={(e) =>
                                    setConfigEdits({
                                      ...configEdits,
                                      strategy_params: {
                                        ...configEdits.strategy_params,
                                        overnight_start_time: e.target.value,
                                      },
                                    })
                                  }
                                  className="w-full px-3 py-2 bg-slate-800 border border-slate-600 rounded text-sm text-white focus:outline-none focus:border-blue-500"
                                />
                              </div>
                              <div>
                                <label className="block text-xs text-slate-400 mb-1">Overnight End Time</label>
                                <input
                                  type="text"
                                  placeholder="09:30"
                                  value={configEdits.strategy_params?.overnight_end_time || '09:30'}
                                  onChange={(e) =>
                                    setConfigEdits({
                                      ...configEdits,
                                      strategy_params: {
                                        ...configEdits.strategy_params,
                                        overnight_end_time: e.target.value,
                                      },
                                    })
                                  }
                                  className="w-full px-3 py-2 bg-slate-800 border border-slate-600 rounded text-sm text-white focus:outline-none focus:border-blue-500"
                                />
                              </div>
                            </div>

                            <div>
                              <label className="block text-xs text-slate-400 mb-1">Market Open Time</label>
                              <input
                                type="text"
                                placeholder="09:30"
                                value={configEdits.strategy_params?.market_open_time || '09:30'}
                                onChange={(e) =>
                                  setConfigEdits({
                                    ...configEdits,
                                    strategy_params: {
                                      ...configEdits.strategy_params,
                                      market_open_time: e.target.value,
                                    },
                                  })
                                }
                                className="w-full px-3 py-2 bg-slate-800 border border-slate-600 rounded text-sm text-white focus:outline-none focus:border-blue-500"
                              />
                            </div>

                            {/* ATR Settings */}
                            <div className="grid grid-cols-2 gap-3">
                              <div>
                                <label className="block text-xs text-slate-400 mb-1">ATR Period</label>
                                <input
                                  type="number"
                                  min="1"
                                  max="100"
                                  value={configEdits.strategy_params?.atr_period || 14}
                                  onChange={(e) =>
                                    setConfigEdits({
                                      ...configEdits,
                                      strategy_params: {
                                        ...configEdits.strategy_params,
                                        atr_period: parseInt(e.target.value) || 14,
                                      },
                                    })
                                  }
                                  className="w-full px-3 py-2 bg-slate-800 border border-slate-600 rounded text-sm text-white focus:outline-none focus:border-blue-500"
                                />
                              </div>
                              <div>
                                <label className="block text-xs text-slate-400 mb-1">ATR Timeframe</label>
                                <select
                                  value={configEdits.strategy_params?.atr_timeframe || '5m'}
                                  onChange={(e) =>
                                    setConfigEdits({
                                      ...configEdits,
                                      strategy_params: {
                                        ...configEdits.strategy_params,
                                        atr_timeframe: e.target.value,
                                      },
                                    })
                                  }
                                  className="w-full px-3 py-2 bg-slate-800 border border-slate-600 rounded text-sm text-white focus:outline-none focus:border-blue-500"
                                >
                                  <option value="1m">1m</option>
                                  <option value="5m">5m</option>
                                  <option value="15m">15m</option>
                                  <option value="1h">1h</option>
                                </select>
                              </div>
                            </div>

                            {/* ATR Multipliers */}
                            <div className="grid grid-cols-2 gap-3">
                              <div>
                                <label className="block text-xs text-slate-400 mb-1">Stop ATR Multiplier</label>
                                <input
                                  type="number"
                                  step="0.1"
                                  min="0.5"
                                  max="5"
                                  value={configEdits.strategy_params?.stop_atr_multiplier || 1.25}
                                  onChange={(e) =>
                                    setConfigEdits({
                                      ...configEdits,
                                      strategy_params: {
                                        ...configEdits.strategy_params,
                                        stop_atr_multiplier: parseFloat(e.target.value) || 1.25,
                                      },
                                    })
                                  }
                                  className="w-full px-3 py-2 bg-slate-800 border border-slate-600 rounded text-sm text-white focus:outline-none focus:border-blue-500"
                                />
                              </div>
                              <div>
                                <label className="block text-xs text-slate-400 mb-1">TP ATR Multiplier</label>
                                <input
                                  type="number"
                                  step="0.1"
                                  min="0.5"
                                  max="10"
                                  value={configEdits.strategy_params?.tp_atr_multiplier || 2.0}
                                  onChange={(e) =>
                                    setConfigEdits({
                                      ...configEdits,
                                      strategy_params: {
                                        ...configEdits.strategy_params,
                                        tp_atr_multiplier: parseFloat(e.target.value) || 2.0,
                                      },
                                    })
                                  }
                                  className="w-full px-3 py-2 bg-slate-800 border border-slate-600 rounded text-sm text-white focus:outline-none focus:border-blue-500"
                                />
                              </div>
                            </div>

                            {/* Breakeven Settings */}
                            <div className="grid grid-cols-2 gap-3">
                              <div>
                                <label className="block text-xs text-slate-400 mb-1">Breakeven Profit Points</label>
                                <input
                                  type="number"
                                  step="0.1"
                                  min="0"
                                  max="100"
                                  value={configEdits.strategy_params?.breakeven_profit_points || 15.0}
                                  onChange={(e) =>
                                    setConfigEdits({
                                      ...configEdits,
                                      strategy_params: {
                                        ...configEdits.strategy_params,
                                        breakeven_profit_points: parseFloat(e.target.value) || 15.0,
                                      },
                                    })
                                  }
                                  className="w-full px-3 py-2 bg-slate-800 border border-slate-600 rounded text-sm text-white focus:outline-none focus:border-blue-500"
                                />
                              </div>
                              <div>
                                <label className="block text-xs text-slate-400 mb-1">Range Break Offset</label>
                                <input
                                  type="number"
                                  step="0.01"
                                  min="0"
                                  max="10"
                                  value={configEdits.strategy_params?.range_break_offset || 0.25}
                                  onChange={(e) =>
                                    setConfigEdits({
                                      ...configEdits,
                                      strategy_params: {
                                        ...configEdits.strategy_params,
                                        range_break_offset: parseFloat(e.target.value) || 0.25,
                                      },
                                    })
                                  }
                                  className="w-full px-3 py-2 bg-slate-800 border border-slate-600 rounded text-sm text-white focus:outline-none focus:border-blue-500"
                                />
                              </div>
                            </div>

                            {/* Breakeven Toggle */}
                            <div className="flex items-center gap-2">
                              <input
                                type="checkbox"
                                checked={configEdits.strategy_params?.breakeven_enabled !== false}
                                onChange={(e) =>
                                  setConfigEdits({
                                    ...configEdits,
                                    strategy_params: {
                                      ...configEdits.strategy_params,
                                      breakeven_enabled: e.target.checked,
                                    },
                                  })
                                }
                                className="w-4 h-4 rounded bg-slate-800 border-slate-600 text-blue-500 focus:ring-blue-500"
                              />
                              <label className="text-xs text-slate-400">Enable Breakeven Stop Management</label>
                            </div>
                          </div>
                        )}

                        {/* Save Button */}
                        <div className="flex gap-2 pt-2">
                          <button
                            onClick={() => saveConfigChanges(strategy.name)}
                            disabled={updateConfigMutation.isLoading || configEdits.symbols.length === 0}
                            className="px-4 py-2 bg-green-600 hover:bg-green-500 text-white rounded text-sm font-medium flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                          >
                            {updateConfigMutation.isLoading ? (
                              <Loader2 className="w-4 h-4 animate-spin" />
                            ) : (
                              <Save className="w-4 h-4" />
                            )}
                            Save Changes
                          </button>
                          <button
                            onClick={() => setEditingConfig(null)}
                            className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded text-sm font-medium transition-colors"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    )}

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

                    {/* Verification Display */}
                    {selectedStrategyForVerify === strategy.name && (
                      <div className="bg-slate-900/50 rounded-lg p-4">
                        <h4 className="text-sm font-semibold text-slate-300 mb-4">Strategy Verification</h4>
                        {verifyLoading ? (
                          <div className="text-center py-4">
                            <Loader2 className="w-6 h-6 animate-spin mx-auto text-slate-400" />
                            <p className="text-sm text-slate-400 mt-2">Verifying strategy...</p>
                          </div>
                        ) : verificationData ? (
                          <div className="space-y-4">
                            <div className={`p-3 rounded-lg border-2 ${
                              verificationData.will_trade 
                                ? 'bg-green-500/10 border-green-500/30' 
                                : 'bg-red-500/10 border-red-500/30'
                            }`}>
                              <div className="flex items-center gap-2 mb-2">
                                {verificationData.will_trade ? (
                                  <CheckCircle2 className="w-5 h-5 text-green-400" />
                                ) : (
                                  <AlertCircle className="w-5 h-5 text-red-400" />
                                )}
                                <span className={`font-semibold ${
                                  verificationData.will_trade ? 'text-green-400' : 'text-red-400'
                                }`}>
                                  {verificationData.will_trade ? '✅ Will Trade' : '❌ Will NOT Trade'}
                                </span>
                              </div>
                              <div className="space-y-1 text-sm">
                                {verificationData.reasons?.map((reason: string, idx: number) => (
                                  <p key={idx} className="text-slate-300">• {reason}</p>
                                ))}
                              </div>
                            </div>
                            
                            <div className="grid grid-cols-2 gap-4 text-sm">
                              <div>
                                <p className="text-slate-500 mb-1">Status</p>
                                <p className="text-slate-200 font-medium">{verificationData.status}</p>
                              </div>
                              <div>
                                <p className="text-slate-500 mb-1">Enabled</p>
                                <p className={`font-medium ${verificationData.enabled ? 'text-green-400' : 'text-red-400'}`}>
                                  {verificationData.enabled ? 'Yes' : 'No'}
                                </p>
                              </div>
                              <div>
                                <p className="text-slate-500 mb-1">Symbols</p>
                                <p className="text-slate-200 font-medium">
                                  {verificationData.symbols?.join(', ') || 'None'}
                                </p>
                              </div>
                              <div>
                                <p className="text-slate-500 mb-1">Position Size</p>
                                <p className="text-slate-200 font-medium">{verificationData.position_size || 'N/A'}</p>
                              </div>
                            </div>
                            
                            {verificationData.next_execution && (
                              <div className="p-3 bg-blue-500/10 border border-blue-500/30 rounded-lg">
                                <p className="text-xs text-slate-400 mb-1">Next Execution</p>
                                <p className="text-lg font-semibold text-blue-300">
                                  {verificationData.next_execution_human || verificationData.next_execution}
                                </p>
                                {verificationData.hours_until_execution && (
                                  <p className="text-sm text-slate-400 mt-1">
                                    In {verificationData.hours_until_execution.toFixed(1)} hours
                                  </p>
                                )}
                                {verificationData.market_open_time && (
                                  <p className="text-xs text-slate-500 mt-1">
                                    Market Open: {verificationData.market_open_time} {verificationData.timezone || 'US/Eastern'}
                                  </p>
                                )}
                              </div>
                            )}
                          </div>
                        ) : (
                          <div className="text-center py-4 text-slate-400">
                            No verification data available
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

