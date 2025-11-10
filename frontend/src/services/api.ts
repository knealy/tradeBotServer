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
} from '../types'

// Auto-detect API base URL
// In production (Railway), API is on same domain, so use empty string for relative URLs
// In development, use localhost:8080
const getApiBaseUrl = () => {
  // If VITE_API_URL is set, use it
  if (import.meta.env.VITE_API_URL) {
    return import.meta.env.VITE_API_URL
  }
  
  // Check if we're in development mode
  const isDev = import.meta.env.DEV || 
                window.location.hostname === 'localhost' || 
                window.location.hostname === '127.0.0.1'
  
  // In production, use relative path (empty string) so it uses same origin
  // In development, use localhost:8080
  return isDev ? 'http://localhost:8080' : ''
}

const API_BASE_URL = getApiBaseUrl()

// Debug logging
const actualIsDev = import.meta.env.DEV || 
                    window.location.hostname === 'localhost' || 
                    window.location.hostname === '127.0.0.1'
console.log('🔧 API Configuration [Build: 2025-11-10T19:30]:', {
  baseURL: API_BASE_URL || '(relative path)',
  actualURL: API_BASE_URL || window.location.origin,
  hostname: window.location.hostname,
  origin: window.location.origin,
  'import.meta.env.DEV': import.meta.env.DEV,
  'computed isDev': actualIsDev
})

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
    'Cache-Control': 'no-cache, no-store, must-revalidate',
    'Pragma': 'no-cache',
    'Expires': '0',
  },
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

  cancelAll: async (): Promise<void> => {
    await api.post('/api/orders/cancel-all')
  },

  placeOrder: async (order: {
    symbol: string
    side: 'BUY' | 'SELL'
    quantity: number
    type?: 'MARKET' | 'LIMIT' | 'STOP'
    price?: number
  }): Promise<Order> => {
    const response = await api.post('/api/orders/place', order)
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

  startStrategy: async (name: string, symbols?: string[]): Promise<{ success: boolean; message: string }> => {
    const response = await api.post(`/api/strategies/${name}/start`, { symbols })
    return response.data
  },

  stopStrategy: async (name: string): Promise<{ success: boolean; message: string }> => {
    const response = await api.post(`/api/strategies/${name}/stop`)
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

// Health check
export const healthApi = {
  check: async (): Promise<{ status: string }> => {
    const response = await api.get('/health')
    return response.data
  },
}

export default api

