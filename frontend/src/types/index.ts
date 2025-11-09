// Account Types
export interface Account {
  id: string
  name: string
  status: string
  balance: number
  currency: string
  account_type: string
}

// Position Types
export interface Position {
  id: string
  symbol: string
  side: 'LONG' | 'SHORT'
  quantity: number
  entry_price: number
  current_price: number
  unrealized_pnl: number
  realized_pnl: number
}

// Order Types
export interface Order {
  id: string
  symbol: string
  side: 'BUY' | 'SELL'
  type: 'MARKET' | 'LIMIT' | 'STOP' | 'BRACKET'
  quantity: number
  price?: number
  status: 'PENDING' | 'FILLED' | 'CANCELLED' | 'REJECTED'
  created_at: string
}

// Strategy Types
export interface Strategy {
  name: string
  status: 'ACTIVE' | 'INACTIVE' | 'PAUSED'
  symbols: string[]
  config: Record<string, any>
  performance: {
    total_trades: number
    winning_trades: number
    losing_trades: number
    win_rate: number
    total_pnl: number
  }
}

// Performance Metrics
export interface PerformanceMetrics {
  system: {
    memory_mb: string
    cpu_percent: string
    uptime: string
  }
  api: {
    total_calls: number
    total_errors: number
    error_rate: number
    slowest_endpoints: Array<{
      endpoint: string
      avg_ms: string
    }>
  }
  cache: Record<string, {
    hits: number
    misses: number
    total: number
    hit_rate: string
  }>
  strategies: Record<string, {
    executions: number
    avg_ms: string
    errors: number
  }>
}

// WebSocket Message Types
export interface WebSocketMessage {
  type: 'account_update' | 'position_update' | 'order_update' | 'metrics_update' | 'error'
  data: any
  timestamp: string
}

