import { useQuery } from 'react-query'
import { strategyApi } from '../services/api'

export default function StrategiesPage() {
  const { data: strategies = [], isLoading } = useQuery(
    'strategies',
    strategyApi.getStrategies,
    {
      refetchInterval: 10000,
    }
  )

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Strategies</h1>
        <p className="text-slate-400 mt-2">Manage your trading strategies</p>
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
                  <div>
                    <h3 className="text-xl font-semibold">{strategy.name}</h3>
                    <p className="text-slate-400 text-sm mt-1">
                      Symbols: {strategy.symbols?.join(', ') || 'N/A'}
                    </p>
                  </div>
                  <div className="flex items-center gap-4">
                    <span
                      className={`px-3 py-1 rounded-full text-sm font-medium ${
                        strategy.enabled
                          ? 'bg-green-900 text-green-200'
                          : 'bg-slate-700 text-slate-300'
                      }`}
                    >
                      {strategy.enabled ? 'Active' : 'Disabled'}
                    </span>
                    {strategy.running && (
                      <span className="px-3 py-1 rounded-full text-sm font-medium bg-blue-900 text-blue-200">
                        Running
                      </span>
                    )}
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

