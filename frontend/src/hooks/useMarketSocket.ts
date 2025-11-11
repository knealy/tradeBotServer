import { useEffect } from 'react'
import { useQueryClient } from 'react-query'
import { wsService } from '../services/websocket'

/**
 * Hook to listen for market updates via WebSocket and invalidate queries
 * This ensures positions, orders, and P&L update in real-time
 */
export const useMarketSocket = () => {
  const queryClient = useQueryClient()

  useEffect(() => {
    const handleMarketUpdate = () => {
      // Invalidate positions and orders queries to trigger refetch
      queryClient.invalidateQueries('positions')
      queryClient.invalidateQueries('orders')
    }

    const handlePositionUpdate = () => {
      queryClient.invalidateQueries('positions')
    }

    const handleOrderUpdate = () => {
      queryClient.invalidateQueries('orders')
    }

    // Listen for market updates
    wsService.on('marketUpdate', handleMarketUpdate)
    wsService.on('position_update', handlePositionUpdate)
    wsService.on('order_update', handleOrderUpdate)

    return () => {
      wsService.off('marketUpdate', handleMarketUpdate)
      wsService.off('position_update', handlePositionUpdate)
      wsService.off('order_update', handleOrderUpdate)
    }
  }, [queryClient])
}

