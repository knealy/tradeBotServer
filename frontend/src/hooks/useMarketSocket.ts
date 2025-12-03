import { useEffect, useRef } from 'react'
import { useQueryClient } from 'react-query'
import { wsService } from '../services/websocket'

/**
 * Hook to listen for market updates via WebSocket and update queries
 * Uses debouncing to prevent excessive API calls
 */
export const useMarketSocket = () => {
  const queryClient = useQueryClient()
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastInvalidateRef = useRef<number>(0)
  const INVALIDATE_DEBOUNCE_MS = 2000 // Only invalidate at most every 2 seconds

  useEffect(() => {
    const debouncedInvalidate = (queryKey: string | string[]) => {
      const now = Date.now()
      // If we invalidated recently, skip this one
      if (now - lastInvalidateRef.current < INVALIDATE_DEBOUNCE_MS) {
        return
      }
      
      // Clear any pending invalidation
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
      }
      
      // Schedule invalidation
      debounceTimerRef.current = setTimeout(() => {
        queryClient.invalidateQueries(queryKey)
        lastInvalidateRef.current = Date.now()
      }, INVALIDATE_DEBOUNCE_MS)
    }

    const handleMarketUpdate = () => {
      // Debounce invalidations to prevent spam
      debouncedInvalidate(['positions', 'orders'])
    }

    const handlePositionUpdate = (data?: any) => {
      // If we have the actual data, update directly instead of invalidating
      if (data?.positions) {
        queryClient.setQueryData(['positions'], data.positions)
      } else {
        debouncedInvalidate('positions')
      }
    }

    const handleOrderUpdate = (data?: any) => {
      // If we have the actual data, update directly instead of invalidating
      if (data?.orders) {
        queryClient.setQueryData(['orders'], data.orders)
      } else {
        debouncedInvalidate('orders')
      }
    }

    // Listen for market updates
    wsService.on('marketUpdate', handleMarketUpdate)
    wsService.on('position_update', handlePositionUpdate)
    wsService.on('order_update', handleOrderUpdate)

    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
      }
      wsService.off('marketUpdate', handleMarketUpdate)
      wsService.off('position_update', handlePositionUpdate)
      wsService.off('order_update', handleOrderUpdate)
    }
  }, [queryClient])
}

