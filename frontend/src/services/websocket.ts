// WebSocket service using native WebSocket (not Socket.io)
// The Python backend uses standard WebSocket, not Socket.io

import type { WebSocketMessage } from '../types'

const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8081'

class WebSocketService {
  private socket: WebSocket | null = null
  private listeners: Map<string, Set<(data: any) => void>> = new Map()
  private reconnectAttempts = 0
  private maxReconnectAttempts = 5
  private reconnectDelay = 1000

  connect(): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      return
    }

    try {
      this.socket = new WebSocket(WS_URL)

      this.socket.onopen = () => {
        console.log('WebSocket connected')
        this.reconnectAttempts = 0
        this.emit('connected', { timestamp: new Date().toISOString() })
      }

      this.socket.onclose = () => {
        console.log('WebSocket disconnected')
        this.attemptReconnect()
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
      }
    } catch (error) {
      console.error('Failed to create WebSocket:', error)
      this.attemptReconnect()
    }
  }

  private attemptReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('Max reconnection attempts reached')
      return
    }

    this.reconnectAttempts++
    const delay = this.reconnectDelay * this.reconnectAttempts

    console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})...`)
    
    setTimeout(() => {
      this.connect()
    }, delay)
  }

  disconnect(): void {
    if (this.socket) {
      this.socket.close()
      this.socket = null
    }
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
      this.socket.send(JSON.stringify({ type: event, data }))
    }
  }

  private handleMessage(message: WebSocketMessage): void {
    const listeners = this.listeners.get(message.type)
    if (listeners) {
      listeners.forEach((callback) => callback(message.data))
    }
  }

  isConnected(): boolean {
    return this.socket?.readyState === WebSocket.OPEN ?? false
  }
}

export const wsService = new WebSocketService()

