export default function SettingsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Settings</h1>
        <p className="text-slate-400 mt-2">Configure your trading bot</p>
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
              <select className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2">
                <option>Auto-select</option>
              </select>
            </div>
          </div>
        </div>

        {/* Trading Settings */}
        <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
          <h2 className="text-xl font-semibold mb-4">Trading Settings</h2>
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="font-medium">Risk Management</p>
                <p className="text-sm text-slate-400">Enable automatic risk controls</p>
              </div>
              <button className="bg-green-600 px-4 py-2 rounded">Enabled</button>
            </div>
            <div className="flex items-center justify-between">
              <div>
                <p className="font-medium">Discord Notifications</p>
                <p className="text-sm text-slate-400">Send trade alerts to Discord</p>
              </div>
              <button className="bg-green-600 px-4 py-2 rounded">Enabled</button>
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
                value="ws://localhost:8081"
                readOnly
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">
                API URL
              </label>
              <input
                type="text"
                value="http://localhost:8080"
                readOnly
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2"
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

