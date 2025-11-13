import { useState, useEffect, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from 'react-query'
import { settingsApi } from '../services/api'
import { useAccount } from '../contexts/AccountContext'

/**
 * Hook to manage widget state with database persistence
 * 
 * @param widgetId - Unique identifier for the widget (e.g., 'positionsOverview', 'riskDrawer')
 * @param defaultValue - Default state value if not found in database
 * @returns [state, setState] - Similar to useState but with persistence
 */
export function useWidgetState<T = boolean>(
  widgetId: string,
  defaultValue: T
): [T, (value: T | ((prev: T) => T)) => void] {
  const { selectedAccount } = useAccount()
  const queryClient = useQueryClient()
  const accountId = selectedAccount?.id

  // Load widget states from settings
  const { data: settingsResponse } = useQuery(
    ['settings', 'global', accountId],
    () => settingsApi.getSettings(accountId || 'global'),
    {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    }
  )

  // Initialize state from settings or use default
  const [state, setStateInternal] = useState<T>(() => {
    const widgetStates = settingsResponse?.settings?.widgetStates || {}
    return widgetStates[widgetId] !== undefined ? widgetStates[widgetId] : defaultValue
  })

  // Update state when settings load
  useEffect(() => {
    if (settingsResponse?.settings?.widgetStates) {
      const widgetStates = settingsResponse.settings.widgetStates
      if (widgetStates[widgetId] !== undefined) {
        setStateInternal(widgetStates[widgetId])
      }
    }
  }, [settingsResponse, widgetId])

  // Save mutation
  const saveMutation = useMutation(
    async (newState: T) => {
      const currentSettings = settingsResponse?.settings || {}
      const widgetStates = currentSettings.widgetStates || {}
      
      const updatedSettings = {
        ...currentSettings,
        widgetStates: {
          ...widgetStates,
          [widgetId]: newState,
        },
      }

      await settingsApi.saveSettings({
        ...updatedSettings,
        account_id: accountId,
      })
    },
    {
      onSuccess: () => {
        // Invalidate settings query to refresh cache
        queryClient.invalidateQueries(['settings'])
      },
    }
  )

  // Wrapper for setState that also saves to database
  const setState = useCallback(
    (value: T | ((prev: T) => T)) => {
      setStateInternal((prev) => {
        const newValue = typeof value === 'function' ? (value as (prev: T) => T)(prev) : value
        
        // Debounce saves to avoid too many API calls
        setTimeout(() => {
          saveMutation.mutate(newValue)
        }, 500)
        
        return newValue
      })
    },
    [saveMutation]
  )

  return [state, setState]
}

