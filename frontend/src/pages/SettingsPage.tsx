import { useState, useEffect } from 'react'
import { useAccount } from '../contexts/AccountContext'
import AccountSelector from '../components/AccountSelector'

export default function SettingsPage() {
  const { accounts, selectedAccount, setSelectedAccount } = useAccount()
  const [defaultAccount, setDefaultAccount] = useState<string>('auto')
  const [riskManagementEnabled, setRiskManagementEnabled] = useState(true)
  const [discordNotificationsEnabled, setDiscordNotificationsEnabled] = useState(true)
  const [wsUrl, setWsUrl] = useState('ws://localhost:8081')
  const [apiUrl, setApiUrl] = useState('http://localhost:8080')
  const [saveStatus, setSaveStatus] = useState<string | null>(null)

  // Load settings from localStorage on mount
  useEffect(() => {
    const savedSettings = localStorage.getItem('tradingBotSettings')
    if (savedSettings) {
      try {
        const settings = JSON.parse(savedSettings)
        setDefaultAccount(settings.defaultAccount || 'auto')
        setRiskManagementEnabled(settings.riskManagementEnabled ?? true)
        setDiscordNotificationsEnabled(settings.discordNotificationsEnabled ?? true)
        setWsUrl(settings.wsUrl || 'ws://localhost:8081')
        setApiUrl(settings.apiUrl || 'http://localhost:8080')
      } catch (e) {
        console.error('Failed to load settings:', e)
      }
    }
  }, [])

  const saveSettings = () => {
    const settings = {
      defaultAccount,
      riskManagementEnabled,
      discordNotificationsEnabled,
      wsUrl,
      apiUrl,
    }
    localStorage.setItem('tradingBotSettings', JSON.stringify(settings))
    setSaveStatus('✅ Settings saved successfully!')
    setTimeout(() => setSaveStatus(null), 3000)
  }

  const toggleRiskManagement = () => {
    setRiskManagementEnabled(!riskManagementEnabled)
  }

  const toggleDiscordNotifications = () => {
    setDiscordNotificationsEnabled(!discordNotificationsEnabled)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Settings</h1>
          <p className="text-slate-400 mt-2">Configure your trading bot</p>
        </div>
        {saveStatus && (
          <div className="bg-green-500/20 border border-green-500/30 rounded-lg px-4 py-2">
            <p className="text-green-400 text-sm">{saveStatus}</p>
          </div>
        )}
      </div>

      {/* Account Selection */}
      <div className="max-w-md">
        <AccountSelector
          accounts={accounts}
          selectedAccount={selectedAccount}
          onAccountChange={setSelectedAccount}
        />
      </div>

      <div className="grid gap-6">
        {/* Account Settings */}
        <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
          <h2 className="text-xl font-semibold mb-4">Account Settings</h2>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">
                Default Account
              </label>
              <select
                value={defaultAccount}
                onChange={(e) => setDefaultAccount(e.target.value)}
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white focus:outline-none focus:border-primary-500"
              >
                <option value="auto">✓ Auto-select</option>
                {accounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name} - ${account.balance?.toLocaleString() || '0'}
                  </option>
                ))}
              </select>
              <p className="text-xs text-slate-400 mt-1">
                The account to use by default when the bot starts
              </p>
            </div>
          </div>
        </div>

        {/* Trading Settings */}
        <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
          <h2 className="text-xl font-semibold mb-4">Trading Settings</h2>
          <div className="space-y-4">
            <div className="flex items-center justify-between py-3 border-b border-slate-700">
              <div>
                <p className="font-medium">Risk Management</p>
                <p className="text-sm text-slate-400">Enable automatic risk controls</p>
              </div>
              <button
                onClick={toggleRiskManagement}
                className={`px-4 py-2 rounded-lg font-semibold text-sm transition-colors ${
                  riskManagementEnabled
                    ? 'bg-green-600 hover:bg-green-500 text-white'
                    : 'bg-slate-600 hover:bg-slate-500 text-white'
                }`}
              >
                {riskManagementEnabled ? 'Enabled' : 'Disabled'}
              </button>
            </div>
            <div className="flex items-center justify-between py-3">
              <div>
                <p className="font-medium">Discord Notifications</p>
                <p className="text-sm text-slate-400">Send trade alerts to Discord</p>
              </div>
              <button
                onClick={toggleDiscordNotifications}
                className={`px-4 py-2 rounded-lg font-semibold text-sm transition-colors ${
                  discordNotificationsEnabled
                    ? 'bg-green-600 hover:bg-green-500 text-white'
                    : 'bg-slate-600 hover:bg-slate-500 text-white'
                }`}
              >
                {discordNotificationsEnabled ? 'Enabled' : 'Disabled'}
              </button>
            </div>
          </div>
        </div>

        {/* System Settings */}
        <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
          <h2 className="text-xl font-semibold mb-4">System Settings</h2>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">
                WebSocket URL
              </label>
              <input
                type="text"
                value={wsUrl}
                onChange={(e) => setWsUrl(e.target.value)}
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white focus:outline-none focus:border-primary-500"
                placeholder="ws://localhost:8081"
              />
              <p className="text-xs text-slate-400 mt-1">
                WebSocket server for real-time updates
              </p>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">
                API URL
              </label>
              <input
                type="text"
                value={apiUrl}
                onChange={(e) => setApiUrl(e.target.value)}
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white focus:outline-none focus:border-primary-500"
                placeholder="http://localhost:8080"
              />
              <p className="text-xs text-slate-400 mt-1">
                Backend API server URL
              </p>
            </div>
          </div>
        </div>

        {/* Save Button */}
        <div className="flex justify-end">
          <button
            onClick={saveSettings}
            className="bg-primary-600 hover:bg-primary-500 text-white px-6 py-3 rounded-lg font-semibold transition-colors"
          >
            Save All Settings
          </button>
        </div>
      </div>
    </div>
  )
}

