import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from 'react-query'
import { Bell, CheckCircle2, AlertTriangle, Info, AlertCircle, ChevronDown, ChevronUp } from 'lucide-react'
import { useAccount } from '../contexts/AccountContext'
import { notificationsApi } from '../services/api'
import { wsService } from '../services/websocket'
import type { Notification } from '../types'

const formatDateTime = (isoTimestamp?: string) => {
  if (!isoTimestamp) return '—'
  try {
    const date = new Date(isoTimestamp)
    const now = new Date()
    const diffMs = now.getTime() - date.getTime()
    const diffMins = Math.floor(diffMs / 60000)
    
    if (diffMins < 1) return 'Just now'
    if (diffMins < 60) return `${diffMins}m ago`
    if (diffMins < 1440) return `${Math.floor(diffMins / 60)}h ago`
    return date.toLocaleString()
  } catch {
    return isoTimestamp
  }
}

const getNotificationIcon = (level: Notification['level']) => {
  switch (level) {
    case 'success':
      return <CheckCircle2 className="w-4 h-4 text-emerald-400" />
    case 'warning':
      return <AlertTriangle className="w-4 h-4 text-amber-400" />
    case 'error':
      return <AlertCircle className="w-4 h-4 text-red-400" />
    default:
      return <Info className="w-4 h-4 text-blue-400" />
  }
}

const getNotificationClass = (level: Notification['level']) => {
  switch (level) {
    case 'success':
      return 'bg-emerald-500/10 border-emerald-500/30 text-emerald-200'
    case 'warning':
      return 'bg-amber-500/10 border-amber-500/30 text-amber-200'
    case 'error':
      return 'bg-red-500/10 border-red-500/30 text-red-200'
    default:
      return 'bg-blue-500/10 border-blue-500/30 text-blue-200'
  }
}

export default function NotificationsFeed() {
  const { selectedAccount } = useAccount()
  const accountId = selectedAccount?.id
  const queryClient = useQueryClient()
  const [isOpen, setIsOpen] = useState(true)
  const [unreadCount, setUnreadCount] = useState(0)

  const { data, isLoading, error } = useQuery(
    ['notifications', accountId],
    () => notificationsApi.getNotifications(accountId),
    {
      enabled: !!accountId,
      staleTime: 10_000,
      refetchInterval: 30_000,
      keepPreviousData: true,
    }
  )

  useEffect(() => {
    const handler = (payload: { account_id: string; notification: Notification }) => {
      if (!payload || !payload.notification) return
      if (accountId && payload.account_id && String(payload.account_id) !== String(accountId)) {
        return
      }
      // Invalidate to refetch
      queryClient.invalidateQueries(['notifications', accountId])
      // Increment unread count if drawer is closed
      if (!isOpen) {
        setUnreadCount((prev) => prev + 1)
      }
    }
    wsService.on('notification', handler)
    return () => {
      wsService.off('notification', handler)
    }
  }, [accountId, queryClient, isOpen])

  // Reset unread count when drawer opens
  useEffect(() => {
    if (isOpen) {
      setUnreadCount(0)
    }
  }, [isOpen])

  const notifications = data?.notifications || []
  const hasNotifications = notifications.length > 0

  return (
    <div className="w-full sm:w-auto sm:max-w-md">
      <div className="bg-slate-800/50 border border-slate-700/50 rounded-xl shadow-sm">
      <button
        onClick={() => setIsOpen(!isOpen)}
          className={`w-full flex items-center justify-between px-4 py-3 rounded-t-lg transition-colors ${
          isOpen
              ? 'bg-slate-800 border-b border-slate-700 text-slate-200'
              : 'bg-slate-800/50 hover:bg-slate-800 text-slate-300'
        }`}
      >
        <div className="flex items-center gap-2">
          <Bell className="w-4 h-4" />
          <span className="text-sm font-semibold">Notifications</span>
          {unreadCount > 0 && (
            <span className="px-1.5 py-0.5 text-xs font-bold bg-red-500 text-white rounded-full min-w-[1.25rem] text-center">
              {unreadCount}
            </span>
          )}
        </div>
          {isOpen ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
      </button>

      {isOpen && (
          <div className="bg-slate-800/50 border-x border-b border-slate-700/50 rounded-b-lg max-h-[32rem] flex flex-col">
          {isLoading ? (
            <div className="p-4 text-center text-slate-400 text-sm">Loading notifications...</div>
          ) : error ? (
            <div className="p-4 text-center text-red-400 text-sm">Failed to load notifications</div>
          ) : !hasNotifications ? (
            <div className="p-4 text-center text-slate-400 text-sm">No notifications</div>
          ) : (
            <div className="overflow-y-auto flex-1">
              {notifications.map((notification) => (
                <div
                  key={notification.id}
                  className={`p-3 border-b border-slate-700/50 ${getNotificationClass(notification.level)}`}
                >
                  <div className="flex items-start gap-2">
                    <div className="mt-0.5">{getNotificationIcon(notification.level)}</div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium">{notification.message}</p>
                      {notification.meta && (
                        <div className="mt-1 text-xs text-slate-400">
                          {notification.meta.symbol && (
                            <span className="mr-2">Symbol: {notification.meta.symbol}</span>
                          )}
                          {notification.meta.side && (
                            <span className="mr-2">Side: {notification.meta.side}</span>
                          )}
                          {notification.meta.quantity && (
                            <span>Qty: {notification.meta.quantity}</span>
                          )}
                        </div>
                      )}
                      <p className="mt-1 text-xs text-slate-500">{formatDateTime(notification.timestamp)}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      </div>
    </div>
  )
}

