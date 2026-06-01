/**
 * 登录 / 注册页面
 *
 * 使用 authStore 处理认证，支持登录和注册两种模式切换。
 * 认证成功后自动跳转到 /wechat。
 */
import { useState, type FormEvent } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '../store/authStore'
import Button from '../components/common/Button'

type Mode = 'login' | 'register'

export default function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { login, register, isAuthenticated } = useAuthStore()

  const [mode, setMode] = useState<Mode>('login')
  const [loginValue, setLoginValue] = useState('')
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // 已登录则跳走
  if (isAuthenticated) {
    const from = (location.state as { from?: string })?.from || '/wechat'
    navigate(from, { replace: true })
    return null
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      if (mode === 'login') {
        await login(loginValue, password)
      } else {
        await register({ email, username, password, display_name: displayName || undefined })
      }
      // 成功 → 跳转到来源页或默认页
      const from = (location.state as { from?: string })?.from || '/wechat'
      navigate(from, { replace: true })
    } catch (err: unknown) {
      const msg =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response: { data?: { detail?: string } } }).response?.data?.detail || '请求失败'
          : '网络错误，请重试'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  const toggleMode = () => {
    setMode(mode === 'login' ? 'register' : 'login')
    setError('')
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-indigo-50 via-white to-purple-50">
      <div className="w-full max-w-sm mx-4">
        {/* Logo / 标题 */}
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-gray-800">AI Girlfriend</h1>
          <p className="text-sm text-gray-400 mt-1">
            {mode === 'login' ? '登录管理控制台' : '创建新账户'}
          </p>
        </div>

        {/* 表单卡片 */}
        <form
          onSubmit={handleSubmit}
          className="bg-white/90 backdrop-blur-sm border border-gray-200 rounded-2xl p-6 shadow-sm space-y-4"
        >
          {/* 错误提示 */}
          {error && (
            <div className="text-sm text-red-500 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
              {error}
            </div>
          )}

          {/* 登录模式：login 字段（email 或 username） */}
          {mode === 'login' ? (
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">邮箱 / 用户名</label>
              <input
                type="text"
                value={loginValue}
                onChange={(e) => setLoginValue(e.target.value)}
                placeholder="请输入邮箱或用户名"
                required
                autoFocus
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400/30 focus:border-indigo-400"
              />
            </div>
          ) : (
            <>
              <div>
                <label className="block text-sm font-medium text-gray-600 mb-1">邮箱</label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="your@email.com"
                  required
                  autoFocus
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400/30 focus:border-indigo-400"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-600 mb-1">用户名</label>
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="至少3个字符"
                  required
                  minLength={3}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400/30 focus:border-indigo-400"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-600 mb-1">显示名称（可选）</label>
                <input
                  type="text"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="你的昵称"
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400/30 focus:border-indigo-400"
                />
              </div>
            </>
          )}

          {/* 密码 */}
          <div>
            <label className="block text-sm font-medium text-gray-600 mb-1">密码</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode === 'register' ? '至少6个字符' : '输入密码'}
              required
              minLength={6}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400/30 focus:border-indigo-400"
            />
          </div>

          {/* 提交按钮 */}
          <Button type="submit" loading={loading} className="w-full" size="lg">
            {mode === 'login' ? '登 录' : '注 册'}
          </Button>

          {/* 切换模式 */}
          <div className="text-center text-sm text-gray-400">
            {mode === 'login' ? (
              <span>
                没有账户？{' '}
                <button type="button" onClick={toggleMode} className="text-indigo-500 hover:text-indigo-600 font-medium">
                  注册
                </button>
              </span>
            ) : (
              <span>
                已有账户？{' '}
                <button type="button" onClick={toggleMode} className="text-indigo-500 hover:text-indigo-600 font-medium">
                  登录
                </button>
              </span>
            )}
          </div>
        </form>

        {/* Footer */}
        <p className="text-center text-xs text-gray-300 mt-6">
          AI Girlfriend Management Console
        </p>
      </div>
    </div>
  )
}
