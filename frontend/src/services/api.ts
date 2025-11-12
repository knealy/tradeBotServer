import axios from 'axios'
import type {
  Account,
  Position,
  Order,
  Strategy,
  PerformanceMetrics,
  TradesResponse,
  PerformanceHistoryResponse,
  HistoricalDataResponse,
  DashboardSettings,
  DashboardSettingsResponse,
  PlaceOrderPayload,
} from '../types'

// Auto-detect API base URL at runtime
// CRITICAL: This must be determined at REQUEST time, not module load time
// Vite/Rollup optimizations can cause issues with module-level constants

const api = axios.create({
  // Use empty string for baseURL - this makes requests relative to current origin
  // This works perfectly for Railway where frontend and API are on same domain
  baseURL: '',
  headers: {
    'Content-Type': 'application/json',
    'Cache-Control': 'no-cache, no-store, must-revalidate',
    'Pragma': 'no-cache',
    'Expires': '0',
  },
})

// Debug logging - Build timestamp helps verify new code is loaded
console.log('🔧 API Configuration [Build: 2025-11-10T20:00 - SIMPLIFIED]:', {
  baseURL: '(empty = relative to current origin)',
  currentOrigin: window.location.origin,
  hostname: window.location.hostname,
  note: 'All API requests will be relative to current page origin'
})

// Add auth token interceptor if needed
api.interceptors.request.use((config) => {
  // Add auth token from localStorage if available
  const token = localStorage.getItem('auth_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

interface SwitchAccountResponse {
  success: boolean
  account?: Account
  message?: string
  error?: string
}

interface GetTradesOptions {
  accountId?: string
  start?: string
  end?: string
  symbol?: string
  type?: 'all' | 'filled' | 'cancelled' | 'pending' | 'rejected'
  limit?: number
  cursor?: string | null
}

interface PerformanceHistoryOptions {
  accountId?: string
  interval?: string
  start?: string
  end?: string
}

interface HistoricalDataOptions {
  symbol: string
  timeframe?: string
  limit?: number
  end?: string
}

// Account API
export const accountApi = {
  getAccounts: async (): Promise<Account[]> => {
    const response = await api.get('/api/accounts')
    return response.data
  },

  getAccountInfo: async (): Promise<Account> => {
    const response = await api.get('/api/account/info')
    return response.data
  },

  switchAccount: async (accountId: string): Promise<SwitchAccountResponse> => {
    const response = await api.post('/api/account/switch', { account_id: accountId })
    return response.data
  },
}

// Position API
export const positionApi = {
  getPositions: async (): Promise<Position[]> => {
    // Add timestamp to prevent browser caching
    const response = await api.get(`/api/positions?_t=${Date.now()}`)
    return response.data
  },

  closePosition: async (positionId: string, quantity?: number): Promise<void> => {
    await api.post(`/api/positions/${positionId}/close`, { quantity })
  },

  modifyStopLoss: async (positionId: string, stopPrice: number): Promise<void> => {
    await api.post(`/api/positions/${positionId}/modify-stop`, { stop_price: stopPrice })
  },

  flattenAll: async (): Promise<void> => {
    await api.post('/api/positions/flatten')
  },
}

// Order API
export const orderApi = {
  getOrders: async (): Promise<Order[]> => {
    // Add timestamp to prevent browser caching
    const response = await api.get(`/api/orders?_t=${Date.now()}`)
    return response.data
  },

  cancelOrder: async (orderId: string): Promise<void> => {
    await api.post(`/api/orders/${orderId}/cancel`)
  },

  cancelAll: async (accountId?: string): Promise<void> => {
    await api.post('/api/orders/cancel-all', accountId ? { account_id: accountId } : {})
  },

  modifyOrder: async (orderId: string, updates: {
    price?: number
    quantity?: number
    order_type?: number
  }): Promise<Order> => {
    const response = await api.post(`/api/orders/${orderId}/modify`, updates)
    return response.data
  },

  placeOrder: async (order: PlaceOrderPayload): Promise<any> => {
    const payload: PlaceOrderPayload = {
      ...order,
    }
    if (payload.symbol) {
      payload.symbol = payload.symbol.trim().toUpperCase()
    }
    if (payload.order_type) {
      payload.order_type = payload.order_type.toLowerCase() as PlaceOrderPayload['order_type']
    }
    const response = await api.post('/api/orders/place', payload)
    return response.data
  },
}

// Strategy API
export const strategyApi = {
  getStrategies: async (): Promise<Strategy[]> => {
    const response = await api.get('/api/strategies')
    return response.data
  },

  getStrategyStatus: async (): Promise<Record<string, Strategy>> => {
    const response = await api.get('/api/strategies/status')
    return response.data
  },

  startStrategy: async (name: string, symbols?: string[], accountId?: string): Promise<{ success: boolean; message: string; error?: string }> => {
    // Always send payload with accountId and symbols (even if empty)
    const payload: { symbols: string[]; accountId?: string } = {
      symbols: symbols ?? []
    }
    if (accountId) payload.accountId = accountId
    const response = await api.post(`/api/strategies/${name}/start`, payload)
    return response.data
  },

  stopStrategy: async (name: string, accountId?: string): Promise<{ success: boolean; message: string; error?: string }> => {
    // Always send payload with accountId
    const payload: { accountId?: string } = {}
    if (accountId) payload.accountId = accountId
    const response = await api.post(`/api/strategies/${name}/stop`, payload)
    return response.data
  },
}

// Metrics API
export const metricsApi = {
  getMetrics: async (): Promise<PerformanceMetrics> => {
    const response = await api.get('/api/metrics')
    // Extract performance data from response
    return response.data.performance || response.data
  },
}

// Trades API
export const tradeApi = {
  getTrades: async (options: GetTradesOptions = {}): Promise<TradesResponse> => {
    const params = new URLSearchParams()
    if (options.accountId) params.append('account_id', options.accountId)
    if (options.start) params.append('start', options.start)
    if (options.end) params.append('end', options.end)
    if (options.symbol) params.append('symbol', options.symbol)
    if (options.type) params.append('type', options.type)
    if (options.limit) params.append('limit', String(options.limit))
    if (options.cursor) params.append('cursor', options.cursor)
    
    // ALWAYS refresh cache to get latest trade data
    params.append('refresh', '1')
    
    // Add timestamp to prevent browser caching
    params.append('_t', Date.now().toString())

    const query = params.toString()
    const response = await api.get(`/api/trades${query ? `?${query}` : ''}`)
    return response.data
  },
}

// Analytics API
export const analyticsApi = {
  getPerformanceSummary: async (): Promise<any> => {
    const response = await api.get('/api/performance')
    return response.data
  },

  getPerformanceHistory: async (options: PerformanceHistoryOptions = {}): Promise<PerformanceHistoryResponse> => {
    const params = new URLSearchParams()
    if (options.accountId) params.append('account_id', options.accountId)
    if (options.interval) params.append('interval', options.interval)
    if (options.start) params.append('start', options.start)
    if (options.end) params.append('end', options.end)

    const query = params.toString()
    const response = await api.get(`/api/performance/history${query ? `?${query}` : ''}`)
    return response.data
  },

  getHistoricalData: async (options: HistoricalDataOptions): Promise<HistoricalDataResponse> => {
    const params = new URLSearchParams()
    params.append('symbol', options.symbol)
    if (options.timeframe) params.append('timeframe', options.timeframe)
    if (options.limit) params.append('limit', String(options.limit))
    if (options.end) params.append('end', options.end)

    const response = await api.get(`/api/history?${params.toString()}`)
    return response.data
  },
}

// Settings API
export const settingsApi = {
  getSettings: async (scope: 'global' | 'current' | string = 'global'): Promise<DashboardSettingsResponse> => {
    const params = scope && scope !== 'global' ? `?account_id=${scope}` : ''
    const response = await api.get(`/api/settings${params}`)
    return response.data
  },

  saveSettings: async (settings: DashboardSettings & { account_id?: string | null; scope?: string }): Promise<{ success: boolean; scope: string }> => {
    const payload = { ...settings }
    const response = await api.post('/api/settings', payload)
    return response.data
  },
}

// Health check
export const healthApi = {
  check: async (): Promise<{ status: string }> => {
    const response = await api.get('/health')
    return response.data
  },
}

export default api

