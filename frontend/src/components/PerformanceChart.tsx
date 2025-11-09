import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts'

// Mock data - will be replaced with real data from API
const mockData = [
  { time: '09:00', pnl: 0, balance: 50000 },
  { time: '10:00', pnl: 150, balance: 50150 },
  { time: '11:00', pnl: 320, balance: 50320 },
  { time: '12:00', pnl: 280, balance: 50280 },
  { time: '13:00', pnl: 450, balance: 50450 },
  { time: '14:00', pnl: 380, balance: 50380 },
  { time: '15:00', pnl: 520, balance: 50520 },
]

export default function PerformanceChart() {
  return (
    <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
      <h2 className="text-xl font-semibold mb-4">Performance Chart</h2>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={mockData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="time" stroke="#9CA3AF" />
          <YAxis stroke="#9CA3AF" />
          <Tooltip
            contentStyle={{
              backgroundColor: '#1E293B',
              border: '1px solid #334155',
              borderRadius: '8px',
            }}
          />
          <Legend />
          <Line
            type="monotone"
            dataKey="pnl"
            stroke="#10B981"
            strokeWidth={2}
            name="P&L"
            dot={{ fill: '#10B981', r: 4 }}
          />
          <Line
            type="monotone"
            dataKey="balance"
            stroke="#3B82F6"
            strokeWidth={2}
            name="Balance"
            dot={{ fill: '#3B82F6', r: 4 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

