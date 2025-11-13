import { useState, useEffect, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from 'react-query'
import { settingsApi } from '../services/api'
import { useAccount } from '../contexts/AccountContext'
import { useDebounce } from './useDebounce'

interface WidgetStates {
  [widgetId: string]: boolean // true = open, false = closed
}

/**
 * Hook to manage widget collapsed/expanded state with database persistence
 * @param widgetId - Unique identifier for the widget (e.g., 'riskDrawer', 'metricsCard')
 * @param defaultOpen - Default state if not found in database
 * @returns [isOpen, setIsOpen] - State and setter function
 */
export function useWidgetState(widgetId: string, defaultOpen: boolean = true) {
  const { selectedAccount } = useAccount()
  const queryClient = useQueryClient()
  const accountId = selectedAccount?.id || 'global'
  
  // Load settings from database
  const { data: settingsResponse } = useQuery(
    ['settings', accountId],
    () => settingsApi.getSettings(accountId === 'global' ? 'global' : accountId),
    {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    }
  )

  // Get initial state from settings or use default
  const widgetStates = (settingsResponse?.settings?.widgetStates as WidgetStates) || {}
  const initialState = widgetStates[widgetId] !== undefined ? widgetStates[widgetId] : defaultOpen
  const [isOpen, setIsOpenState] = useState(initialState)

  // Update local state when settings load
  useEffect(() => {
    if (settingsResponse?.settings?.widgetStates) {
      const states = settingsResponse.settings.widgetStates as WidgetStates
      if (states[widgetId] !== undefined) {
        setIsOpenState(states[widgetId])
      }
    }
  }, [settingsResponse, widgetId])

  // Debounce state changes to avoid too many API calls
  const debouncedIsOpen = useDebounce(isOpen, 500)

  // Save mutation
  const saveMutation = useMutation(
    (newStates: WidgetStates) => {
      const currentSettings = settingsResponse?.settings || {}
      return settingsApi.saveSettings({
        ...currentSettings,
        widgetStates: newStates,
        account_id: accountId === 'global' ? null : accountId,
      })
    },
    {
      onSuccess: () => {
        queryClient.invalidateQueries(['settings', accountId])
      },
    }
  )

  // Save state changes to database
  useEffect(() => {
    if (!settingsResponse?.settings) return // Don't save until settings are loaded

    const currentStates = (settingsResponse.settings.widgetStates as WidgetStates) || {}
    if (currentStates[widgetId] === debouncedIsOpen) return // No change

    const newStates = {
      ...currentStates,
      [widgetId]: debouncedIsOpen,
    }

    saveMutation.mutate(newStates)
  }, [debouncedIsOpen, widgetId, settingsResponse, saveMutation])

  // Setter function
  const setIsOpen = useCallback((value: boolean | ((prev: boolean) => boolean)) => {
    setIsOpenState(value)
  }, [])

  return [isOpen, setIsOpen] as const
}

