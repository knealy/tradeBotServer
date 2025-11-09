import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { wsService } from '../services/websocket'

type WebSocketStatus = 'connecting' | 'connected' | 'reconnecting' | 'disconnected' | 'error'

interface WebSocketContextValue {
  status: WebSocketStatus
  reconnectAttempts: number
  lastConnectedAt: string | null
  lastDisconnectedAt: string | null
  lastError: string | null
  reconnect: () => void
  isConnected: boolean
}

const WebSocketContext = createContext<WebSocketContextValue | undefined>(undefined)

export function WebSocketProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<WebSocketStatus>('connecting')
  const [reconnectAttempts, setReconnectAttempts] = useState(0)
  const [lastConnectedAt, setLastConnectedAt] = useState<string | null>(null)
  const [lastDisconnectedAt, setLastDisconnectedAt] = useState<string | null>(null)
  const [lastError, setLastError] = useState<string | null>(null)

  useEffect(() => {
    const handleConnecting = () => {
      setStatus('connecting')
      setLastError(null)
    }

    const handleConnected = (data?: { timestamp?: string }) => {
      setStatus('connected')
      setReconnectAttempts(0)
      setLastConnectedAt(data?.timestamp ?? new Date().toISOString())
      setLastError(null)
    }

    const handleDisconnected = (data?: { timestamp?: string }) => {
      setStatus('disconnected')
      setLastDisconnectedAt(data?.timestamp ?? new Date().toISOString())
    }

    const handleReconnecting = (data?: { attempt?: number }) => {
      setStatus('reconnecting')
      if (data?.attempt) {
        setReconnectAttempts(data.attempt)
      } else {
        setReconnectAttempts((prev) => prev + 1)
      }
    }

    const handleError = (data?: { error?: any }) => {
      setStatus('error')
      const message = data?.error instanceof Event ? data.error.type : data?.error?.message ?? 'Unknown error'
      setLastError(message)
    }

    const handleFailed = (data?: { attempts?: number }) => {
      setStatus('error')
      if (data?.attempts) {
        setReconnectAttempts(data.attempts)
      }
    }

    wsService.on('socket_connecting', handleConnecting)
    wsService.on('socket_connected', handleConnected)
    wsService.on('socket_disconnected', handleDisconnected)
    wsService.on('socket_reconnecting', handleReconnecting)
    wsService.on('socket_error', handleError)
    wsService.on('socket_failed', handleFailed)

    wsService.connect()

    return () => {
      wsService.off('socket_connecting', handleConnecting)
      wsService.off('socket_connected', handleConnected)
      wsService.off('socket_disconnected', handleDisconnected)
      wsService.off('socket_reconnecting', handleReconnecting)
      wsService.off('socket_error', handleError)
      wsService.off('socket_failed', handleFailed)
    }
  }, [])

  const reconnect = useCallback(() => {
    setStatus('connecting')
    setLastError(null)
    setReconnectAttempts(0)
    wsService.forceReconnect()
  }, [])

  const value = useMemo<WebSocketContextValue>(
    () => ({
      status,
      reconnectAttempts,
      lastConnectedAt,
      lastDisconnectedAt,
      lastError,
      reconnect,
      isConnected: wsService.isConnected(),
    }),
    [status, reconnectAttempts, lastConnectedAt, lastDisconnectedAt, lastError, reconnect]
  )

  return <WebSocketContext.Provider value={value}>{children}</WebSocketContext.Provider>
}

export function useWebSocket(): WebSocketContextValue {
  const context = useContext(WebSocketContext)
  if (!context) {
    throw new Error('useWebSocket must be used within a WebSocketProvider')
  }
  return context
}

