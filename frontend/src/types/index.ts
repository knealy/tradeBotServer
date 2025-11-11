// Account Types
export interface Account {
  id: string
  name: string
  status: string
  balance: number
  currency: string
  account_type: string
  accountId?: string
  account_id?: string
  equity?: number
  dailyPnL?: number
  daily_pnl?: number
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
  status: 'ACTIVE' | 'INACTIVE' | 'PAUSED' | 'running' | 'stopped'
  symbols: string[]
  config?: Record<string, any>
  description?: string
  stats?: {
    total_trades?: number
    winning_trades?: number
    losing_trades?: number
    win_rate?: number
    total_pnl?: number
    avg_pnl?: number
  }
  performance?: {
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

export interface PerformanceHistoryPoint {
  timestamp: string
  period_pnl: number
  cumulative_pnl: number
  trade_count: number
  winning_trades: number
  losing_trades: number
  max_drawdown: number
}

export interface PerformanceHistorySummary {
  start_balance: number
  end_balance: number
  total_pnl: number
  win_rate: number
  avg_win: number
  avg_loss: number
  max_drawdown: number
  trade_count: number
  winning_trades: number
  losing_trades: number
}

export interface PerformanceHistoryResponse {
  account_id: string
  interval: string
  start: string
  end: string
  points: PerformanceHistoryPoint[]
  summary: PerformanceHistorySummary
}

export interface Trade {
  id: string
  order_id: string
  symbol: string
  side: string
  quantity: number
  price?: number | null
  pnl: number
  fees: number
  net_pnl: number
  status: string
  strategy?: string
  timestamp: string
}

export interface TradesResponse {
  account_id: string
  start: string
  end: string
  items: Trade[]
  next_cursor?: string | null
  summary: {
    total: number
    filled: number
    cancelled: number
    pending: number
    rejected: number
    gross_pnl: number
    net_pnl: number
    fees: number
    displayed_count?: number
    total_in_period?: number
  }
}

export interface HistoricalBar {
  timestamp: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface HistoricalDataResponse {
  symbol: string
  timeframe: string
  count: number
  bars: HistoricalBar[]
}

