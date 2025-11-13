import { PerformanceMetrics } from '../types'
import { Activity, Zap, Database, ChevronDown, ChevronUp } from 'lucide-react'
import { useWidgetState } from '../hooks/useWidgetState'

interface MetricsCardProps {
  metrics: PerformanceMetrics
}

export default function MetricsCard({ metrics }: MetricsCardProps) {
  const [isOpen, setIsOpen] = useWidgetState('metricsCard', true)

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl shadow-sm">
      <button
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        className="w-full flex items-center justify-between px-4 py-3"
      >
        <div className="flex items-center gap-3 text-left">
          <Activity className="w-5 h-5 text-blue-400" />
          <div>
            <p className="text-sm font-semibold text-slate-200">Performance Metrics</p>
            <p className="text-xs text-slate-400">System, API & Cache stats</p>
          </div>
        </div>
        {isOpen ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
      </button>

      {isOpen && (
        <div className="px-4 pb-4">
          <div className="space-y-4">
        {/* System Metrics */}
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-slate-400 flex items-center gap-2">
            <Activity className="w-4 h-4" />
            System
          </h3>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <div>
              <p className="text-slate-400">Memory</p>
              <p className="font-semibold">{metrics.system.memory_mb} MB</p>
            </div>
            <div>
              <p className="text-slate-400">CPU</p>
              <p className="font-semibold">{metrics.system.cpu_percent}%</p>
            </div>
            <div className="col-span-2">
              <p className="text-slate-400">Uptime</p>
              <p className="font-semibold">{metrics.system.uptime}</p>
            </div>
          </div>
        </div>

        {/* API Metrics */}
        <div className="space-y-2 pt-4 border-t border-slate-700">
          <h3 className="text-sm font-medium text-slate-400 flex items-center gap-2">
            <Zap className="w-4 h-4" />
            API Calls
          </h3>
          <div className="space-y-1 text-sm">
            <div className="flex justify-between">
              <span className="text-slate-400">Total</span>
              <span className="font-semibold">{metrics.api.total_calls}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Errors</span>
              <span className={`font-semibold ${
                metrics.api.total_errors > 0 ? 'text-red-400' : 'text-green-400'
              }`}>
                {metrics.api.total_errors}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Error Rate</span>
              <span className={`font-semibold ${
                metrics.api.error_rate > 1 ? 'text-red-400' : 'text-green-400'
              }`}>
                {metrics.api.error_rate.toFixed(2)}%
              </span>
            </div>
          </div>
        </div>

        {/* Cache Metrics */}
        {Object.keys(metrics.cache).length > 0 && (
          <div className="space-y-2 pt-4 border-t border-slate-700">
            <h3 className="text-sm font-medium text-slate-400 flex items-center gap-2">
              <Database className="w-4 h-4" />
              Cache
            </h3>
            <div className="space-y-2">
              {Object.entries(metrics.cache).map(([key, cache]) => (
                <div key={key} className="text-sm">
                  <div className="flex justify-between mb-1">
                    <span className="text-slate-400 text-xs">{key}</span>
                    <span className="font-semibold">{cache.hit_rate}</span>
                  </div>
                  <div className="w-full bg-slate-700 rounded-full h-1.5">
                    <div
                      className="bg-primary-500 h-1.5 rounded-full transition-all"
                      style={{ width: cache.hit_rate }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
          </div>
        </div>
      )}
    </div>
  )
}

