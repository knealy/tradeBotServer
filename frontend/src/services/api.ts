import axios from 'axios'
import type { Account, Position, Order, Strategy, PerformanceMetrics } from '../types'

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8080'

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
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

  switchAccount: async (accountId: string): Promise<Account> => {
    const response = await api.post(`/api/account/switch`, { account_id: accountId })
    return response.data
  },
}

// Position API
export const positionApi = {
  getPositions: async (): Promise<Position[]> => {
    const response = await api.get('/api/positions')
    return response.data
  },

  closePosition: async (positionId: string, quantity?: number): Promise<void> => {
    await api.post(`/api/positions/${positionId}/close`, { quantity })
  },
}

// Order API
export const orderApi = {
  getOrders: async (): Promise<Order[]> => {
    const response = await api.get('/api/orders')
    return response.data
  },

  cancelOrder: async (orderId: string): Promise<void> => {
    await api.post(`/api/orders/${orderId}/cancel`)
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

  startStrategy: async (name: string, symbols?: string[]): Promise<void> => {
    await api.post(`/api/strategies/${name}/start`, { symbols })
  },

  stopStrategy: async (name: string): Promise<void> => {
    await api.post(`/api/strategies/${name}/stop`)
  },
}

// Metrics API
export const metricsApi = {
  getMetrics: async (): Promise<PerformanceMetrics> => {
    const response = await api.get('/api/metrics')
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

