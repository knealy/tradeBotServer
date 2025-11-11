import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { wsService } from '../services/websocket'

/**
 * Hook to listen for market updates via WebSocket and invalidate queries
 * This ensures positions, orders, and P&L update in real-time
 */
export const useMarketSocket = () => {
  const queryClient = useQueryClient()

  useEffect(() => {
    const handleMarketUpdate = (data: any) => {
      // Invalidate positions and orders queries to trigger refetch
      queryClient.invalidateQueries({ queryKey: ['positions'] })
      queryClient.invalidateQueries({ queryKey: ['orders'] })
      
      // Optional: Optimistic update for positions if we have the data
      if (data.symbol && data.bid && data.ask) {
        queryClient.setQueryData(['positions'], (old: any[]) => {
          if (!Array.isArray(old)) return old
          
          return old.map((p: any) => {
            if (p.symbol === data.symbol) {
              const currentPrice = data.bid || data.ask || data.last || p.current_price
              // Recalculate unrealized P&L
              const entryPrice = p.entry_price || 0
              const quantity = p.quantity || 0
              const side = p.side || 'LONG'
              
              let unrealizedPnl = 0
              if (entryPrice && currentPrice && quantity) {
                const priceDiff = side === 'LONG' 
                  ? currentPrice - entryPrice 
                  : entryPrice - currentPrice
                // Assuming point value of 1 for now (should be symbol-specific)
                unrealizedPnl = priceDiff * quantity
              }
              
              return {
                ...p,
                current_price: currentPrice,
                unrealized_pnl: unrealizedPnl
              }
            }
            return p
          })
        })
      }
    }

    const handlePositionUpdate = (data: any) => {
      // Invalidate to trigger refetch with latest data
      queryClient.invalidateQueries({ queryKey: ['positions'] })
    }

    const handleOrderUpdate = (data: any) => {
      // Invalidate to trigger refetch with latest data
      queryClient.invalidateQueries({ queryKey: ['orders'] })
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

