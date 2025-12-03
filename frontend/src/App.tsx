import { QueryClient, QueryClientProvider } from 'react-query'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AccountProvider } from './contexts/AccountContext'
import Dashboard from './components/Dashboard'
import PositionsPage from './pages/PositionsPage'
import StrategiesPage from './pages/StrategiesPage'
import SettingsPage from './pages/SettingsPage'
import Layout from './components/Layout'
import { WebSocketProvider } from './contexts/WebSocketContext'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 30000), // Exponential backoff
    },
  },
})

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <WebSocketProvider>
        <AccountProvider>
          <BrowserRouter>
            <Layout>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/positions" element={<PositionsPage />} />
                <Route path="/strategies" element={<StrategiesPage />} />
                <Route path="/settings" element={<SettingsPage />} />
              </Routes>
            </Layout>
          </BrowserRouter>
        </AccountProvider>
      </WebSocketProvider>
    </QueryClientProvider>
  )
}

export default App

