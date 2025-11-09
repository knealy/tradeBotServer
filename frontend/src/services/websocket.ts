// WebSocket service using native WebSocket (not Socket.io)
// The Python backend uses standard WebSocket, not Socket.io

import type { WebSocketMessage } from '../types'

const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8081'

class WebSocketService {
  private socket: WebSocket | null = null
  private listeners: Map<string, Set<(data: any) => void>> = new Map()
  private reconnectAttempts = 0
  private maxReconnectAttempts = 8
  private reconnectDelay = 1000
  private heartbeatIntervalId: number | null = null
  private readonly HEARTBEAT_INTERVAL = 30000
  private manualClose = false

  connect(): void {
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      return
    }

    this.notify('socket_connecting', { timestamp: new Date().toISOString() })

    try {
      this.manualClose = false
      this.socket = new WebSocket(WS_URL)

      this.socket.onopen = () => {
        console.log('WebSocket connected')
        this.reconnectAttempts = 0
        this.notify('socket_connected', { timestamp: new Date().toISOString() })
        this.startHeartbeat()
      }

      this.socket.onclose = (event) => {
        console.log('WebSocket disconnected', event.reason || '')
        this.clearHeartbeat()
        this.notify('socket_disconnected', {
          code: event.code,
          reason: event.reason,
          wasClean: event.wasClean,
          timestamp: new Date().toISOString(),
        })

        if (!this.manualClose) {
          this.attemptReconnect()
        }
      }

      this.socket.onmessage = (event) => {
        try {
          const message: WebSocketMessage = JSON.parse(event.data)
          this.handleMessage(message)
        } catch (e) {
          console.error('Error parsing WebSocket message:', e)
        }
      }

      this.socket.onerror = (error) => {
        console.error('WebSocket error:', error)
        this.notify('socket_error', { error })
      }
    } catch (error) {
      console.error('Failed to create WebSocket:', error)
      this.notify('socket_error', { error })
      this.attemptReconnect()
    }
  }

  private attemptReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('Max reconnection attempts reached')
      this.notify('socket_failed', {
        attempts: this.reconnectAttempts,
        timestamp: new Date().toISOString(),
      })
      return
    }

    this.reconnectAttempts++
    const delay = this.reconnectDelay * this.reconnectAttempts

    console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})...`)
    this.notify('socket_reconnecting', {
      attempt: this.reconnectAttempts,
      delay,
      timestamp: new Date().toISOString(),
    })

    window.setTimeout(() => {
      this.connect()
    }, delay)
  }

  disconnect(): void {
    this.manualClose = true
    this.clearHeartbeat()
    if (this.socket) {
      this.socket.close()
      this.socket = null
    }
  }

  forceReconnect(): void {
    this.manualClose = false
    this.reconnectAttempts = 0
    this.clearHeartbeat()
    if (this.socket) {
      this.socket.close()
      this.socket = null
    }
    this.connect()
  }

  on(event: string, callback: (data: any) => void): void {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set())
    }
    this.listeners.get(event)!.add(callback)
  }

  off(event: string, callback: (data: any) => void): void {
    this.listeners.get(event)?.delete(callback)
  }

  emit(event: string, data: any): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ action: event, payload: data }))
    }
  }

  private handleMessage(message: WebSocketMessage): void {
    const listeners = this.listeners.get(message.type)
    if (listeners) {
      listeners.forEach((callback) => callback(message.data))
    }
  }

  private notify(event: string, data: any): void {
    const listeners = this.listeners.get(event)
    if (listeners) {
      listeners.forEach((callback) => callback(data))
    }
  }

  private startHeartbeat(): void {
    this.clearHeartbeat()
    this.heartbeatIntervalId = window.setInterval(() => {
      if (this.socket?.readyState === WebSocket.OPEN) {
        this.socket.send(JSON.stringify({ action: 'ping', payload: { timestamp: Date.now() } }))
      }
    }, this.HEARTBEAT_INTERVAL)
  }

  private clearHeartbeat(): void {
    if (this.heartbeatIntervalId !== null) {
      window.clearInterval(this.heartbeatIntervalId)
      this.heartbeatIntervalId = null
    }
  }

  isConnected(): boolean {
    return this.socket?.readyState === WebSocket.OPEN || false
  }
}

export const wsService = new WebSocketService()

