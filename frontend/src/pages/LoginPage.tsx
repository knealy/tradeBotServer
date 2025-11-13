import { useState, useEffect } from 'react'
import { useMutation } from 'react-query'
import { useNavigate } from 'react-router-dom'
import { authApi } from '../services/api'
import { Lock, LogIn } from 'lucide-react'

export default function LoginPage() {
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  
  // Check if already logged in
  useEffect(() => {
    const token = localStorage.getItem('auth_token')
    if (token) {
      navigate('/')
    }
  }, [navigate])
  
  const loginMutation = useMutation(
    ({ username, password }: { username: string; password: string }) => authApi.login(username, password),
    {
      onSuccess: (data) => {
        localStorage.setItem('auth_token', data.token)
        navigate('/')
      },
      onError: (error: any) => {
        setError(error?.response?.data?.error || 'Login failed')
      },
    }
  )
  
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    loginMutation.mutate({ username, password })
  }
  
  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-900 p-4">
      <div className="bg-slate-800 rounded-lg p-8 border border-slate-700 max-w-md w-full">
        <div className="flex items-center gap-3 mb-6">
          <Lock className="w-8 h-8 text-blue-400" />
          <h1 className="text-2xl font-bold">Login</h1>
        </div>
        
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1">
              Username
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="Enter username"
            />
          </div>
          
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="Enter password"
            />
          </div>
          
          {error && (
            <div className="p-3 bg-red-500/10 border border-red-500/30 rounded text-red-300 text-sm">
              {error}
            </div>
          )}
          
          <button
            type="submit"
            disabled={loginMutation.isLoading}
            className="w-full px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 disabled:opacity-50 text-white rounded-lg transition-colors flex items-center justify-center gap-2"
          >
            <LogIn className="w-4 h-4" />
            {loginMutation.isLoading ? 'Logging in...' : 'Login'}
          </button>
        </form>
        
        <p className="mt-4 text-xs text-slate-400 text-center">
          Default: admin / admin (change via ADMIN_USERNAME/ADMIN_PASSWORD env vars)
        </p>
      </div>
    </div>
  )
}

