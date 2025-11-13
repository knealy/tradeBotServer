import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from 'react-query'
import { adminApi } from '../services/api'
import { Shield, Users, BarChart3, Plus, Trash2, Edit, Lock } from 'lucide-react'

export default function AdminPanel() {
  const queryClient = useQueryClient()
  const [showCreateUser, setShowCreateUser] = useState(false)
  const [newUser, setNewUser] = useState({ username: '', password: '', email: '', role: 'user' })
  
  const { data: usersData, isLoading: usersLoading } = useQuery(
    ['admin', 'users'],
    adminApi.listUsers,
    {
      staleTime: 30_000,
      retry: false,
    }
  )
  
  const { data: statsData, isLoading: statsLoading } = useQuery(
    ['admin', 'stats'],
    adminApi.getStats,
    {
      staleTime: 10_000,
      refetchInterval: 30_000,
    }
  )
  
  const createUserMutation = useMutation(adminApi.createUser, {
    onSuccess: () => {
      queryClient.invalidateQueries(['admin', 'users'])
      setShowCreateUser(false)
      setNewUser({ username: '', password: '', email: '', role: 'user' })
    },
  })
  
  const users = usersData?.users || []
  const stats = statsData || {}
  
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-3">
            <Shield className="w-8 h-8 text-yellow-400" />
            Admin Panel
          </h1>
          <p className="text-slate-400 mt-2">Manage users and monitor system</p>
        </div>
        <button
          onClick={() => setShowCreateUser(true)}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors flex items-center gap-2"
        >
          <Plus className="w-4 h-4" />
          Create User
        </button>
      </div>
      
      {/* System Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
          <div className="flex items-center gap-3 mb-2">
            <Users className="w-5 h-5 text-blue-400" />
            <h3 className="text-lg font-semibold">Users</h3>
          </div>
          {statsLoading ? (
            <p className="text-slate-400">Loading...</p>
          ) : (
            <div>
              <p className="text-3xl font-bold">{stats.users?.total || 0}</p>
              <p className="text-sm text-slate-400">{stats.users?.active || 0} active</p>
            </div>
          )}
        </div>
        
        <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
          <div className="flex items-center gap-3 mb-2">
            <BarChart3 className="w-5 h-5 text-green-400" />
            <h3 className="text-lg font-semibold">Trading</h3>
          </div>
          {statsLoading ? (
            <p className="text-slate-400">Loading...</p>
          ) : (
            <div>
              <p className="text-3xl font-bold">{stats.trading?.active_strategies || 0}</p>
              <p className="text-sm text-slate-400">Active strategies</p>
            </div>
          )}
        </div>
        
        <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
          <div className="flex items-center gap-3 mb-2">
            <Lock className="w-5 h-5 text-purple-400" />
            <h3 className="text-lg font-semibold">Cache</h3>
          </div>
          {statsLoading ? (
            <p className="text-slate-400">Loading...</p>
          ) : (
            <div>
              <p className="text-lg font-bold">
                {stats.cache?.redis?.enabled ? '✅ Redis' : '⚠️ Fallback'}
              </p>
              <p className="text-sm text-slate-400">
                {stats.cache?.redis?.total_keys || 0} keys
              </p>
            </div>
          )}
        </div>
      </div>
      
      {/* Users List */}
      <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
        <h2 className="text-xl font-semibold mb-4 flex items-center gap-2">
          <Users className="w-5 h-5" />
          Users ({users.length})
        </h2>
        
        {usersLoading ? (
          <div className="text-center py-8">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-500 mx-auto"></div>
            <p className="text-slate-400 mt-2">Loading users...</p>
          </div>
        ) : users.length === 0 ? (
          <p className="text-slate-400 text-center py-8">No users found</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-slate-400 text-xs uppercase tracking-wide border-b border-slate-700">
                  <th className="pb-3 text-left">Username</th>
                  <th className="pb-3 text-left">Email</th>
                  <th className="pb-3 text-left">Role</th>
                  <th className="pb-3 text-left">Status</th>
                  <th className="pb-3 text-left">Last Login</th>
                  <th className="pb-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/60">
                {users.map((user: any) => (
                  <tr key={user.id} className="hover:bg-slate-700/40 transition-colors">
                    <td className="py-3 font-semibold">{user.username}</td>
                    <td className="py-3 text-slate-300">{user.email || '—'}</td>
                    <td className="py-3">
                      <span className={`px-2 py-1 rounded text-xs ${
                        user.role === 'admin' 
                          ? 'bg-yellow-500/20 text-yellow-400' 
                          : 'bg-blue-500/20 text-blue-400'
                      }`}>
                        {user.role}
                      </span>
                    </td>
                    <td className="py-3">
                      <span className={`px-2 py-1 rounded text-xs ${
                        user.is_active 
                          ? 'bg-green-500/20 text-green-400' 
                          : 'bg-red-500/20 text-red-400'
                      }`}>
                        {user.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td className="py-3 text-slate-400 text-xs">
                      {user.last_login 
                        ? new Date(user.last_login).toLocaleString() 
                        : 'Never'}
                    </td>
                    <td className="py-3 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          className="p-1.5 hover:bg-blue-500/20 text-blue-400 rounded transition-colors"
                          title="Edit user"
                        >
                          <Edit className="w-4 h-4" />
                        </button>
                        <button
                          className="p-1.5 hover:bg-red-500/20 text-red-400 rounded transition-colors"
                          title="Delete user"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      
      {/* Create User Modal */}
      {showCreateUser && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-slate-800 rounded-lg p-6 border border-slate-700 max-w-md w-full mx-4">
            <h2 className="text-xl font-semibold mb-4">Create New User</h2>
            
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1">
                  Username
                </label>
                <input
                  type="text"
                  value={newUser.username}
                  onChange={(e) => setNewUser({ ...newUser, username: e.target.value })}
                  className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1">
                  Password
                </label>
                <input
                  type="password"
                  value={newUser.password}
                  onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
                  className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1">
                  Email (optional)
                </label>
                <input
                  type="email"
                  value={newUser.email}
                  onChange={(e) => setNewUser({ ...newUser, email: e.target.value })}
                  className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1">
                  Role
                </label>
                <select
                  value={newUser.role}
                  onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}
                  className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="user">User</option>
                  <option value="admin">Admin</option>
                </select>
              </div>
            </div>
            
            <div className="flex items-center gap-3 mt-6">
              <button
                onClick={() => createUserMutation.mutate(newUser)}
                disabled={!newUser.username || !newUser.password || createUserMutation.isLoading}
                className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 disabled:opacity-50 text-white rounded-lg transition-colors"
              >
                {createUserMutation.isLoading ? 'Creating...' : 'Create User'}
              </button>
              <button
                onClick={() => {
                  setShowCreateUser(false)
                  setNewUser({ username: '', password: '', email: '', role: 'user' })
                }}
                className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors"
              >
                Cancel
              </button>
            </div>
            
            {createUserMutation.isError && (
              <div className="mt-4 p-3 bg-red-500/10 border border-red-500/30 rounded text-red-300 text-sm">
                {(createUserMutation.error as any)?.response?.data?.error || 'Failed to create user'}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

