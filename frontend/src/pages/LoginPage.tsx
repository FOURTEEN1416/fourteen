/**
 * 登录 / 注册页面
 *
 * 使用 useAuth hook 处理认证，支持登录和注册两种模式切换。
 * 认证成功后自动跳转到 /wechat。
 */
import { useState, type FormEvent } from 'react'
import { useNavigate, useLocation, Navigate, Link } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

type Mode = 'login' | 'register'

export default function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { login, register, registerWithInvite, isAuthenticated } = useAuth()

  const [mode, setMode] = useState<Mode>('login')
  const [loginValue, setLoginValue] = useState('')
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const [useInvite, setUseInvite] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // 已登录则跳走（用 <Navigate> 组件，避免 render 期间副作用）
  if (isAuthenticated) {
    const from = (location.state as { from?: string })?.from || '/wechat'
    return <Navigate to={from} replace />
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      if (mode === 'login') {
        await login(loginValue, password)
      } else if (useInvite && inviteCode.trim()) {
        await registerWithInvite({
          invite_code: inviteCode.trim(),
          email, username, password,
          display_name: displayName || undefined,
        })
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
    <div className="min-h-[100dvh] flex items-center justify-center">
      <div className="w-full max-w-sm mx-4">
        {/* Logo / 标题 */}
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold bg-gradient-to-r from-pink-500 via-blue-500 to-green-500 bg-clip-text text-transparent">唯一的你——十四</h1>
          <p className="text-sm text-gray-400 mt-1">
            {mode === 'login' ? '登录管理控制台' : '创建新账户'}
          </p>
        </div>

        {/* 表单卡片 */}
        <form
          onSubmit={handleSubmit}
          className="glass-card rounded-2xl p-6 space-y-4"
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
                className="input-macaron w-full glass-card rounded-lg px-3 py-2.5 text-sm"
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
                  className="input-macaron w-full glass-card rounded-lg px-3 py-2.5 text-sm"
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
                  className="input-macaron w-full glass-card rounded-lg px-3 py-2.5 text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-600 mb-1">显示名称（可选）</label>
                <input
                  type="text"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="你的昵称"
                  className="input-macaron w-full glass-card rounded-lg px-3 py-2.5 text-sm"
                />
              </div>

              {/* 邀请码切换 */}
              <div className="flex items-center gap-2">
                <input
                  id="useInvite"
                  type="checkbox"
                  checked={useInvite}
                  onChange={(e) => setUseInvite(e.target.checked)}
                  className="rounded border-gray-300 text-pink-500 focus:ring-pink-400/30"
                />
                <label htmlFor="useInvite" className="text-sm text-gray-500 cursor-pointer select-none">
                  我有邀请码
                </label>
              </div>

              {/* 邀请码输入 */}
              {useInvite && (
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1">邀请码</label>
                  <input
                    type="text"
                    value={inviteCode}
                    onChange={(e) => setInviteCode(e.target.value)}
                    placeholder="请输入8位邀请码"
                    required={useInvite}
                    maxLength={16}
                    className="input-macaron w-full glass-card rounded-lg px-3 py-2.5 text-sm"
                  />
                </div>
              )}
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
              className="input-macaron w-full glass-card rounded-lg px-3 py-2.5 text-sm"
            />
          </div>

          {/* 提交按钮 */}
          <button
            type="submit"
            disabled={loading}
            className="btn-macaron w-full rounded-lg py-2.5 text-sm font-medium disabled:opacity-50"
          >
            {loading ? '处理中...' : mode === 'login' ? '登 录' : '注 册'}
          </button>

          {/* 切换模式 */}
          <div className="text-center text-xs text-gray-400">
            {mode === 'login' ? (
              <span>
                没有账户？{' '}
                <button type="button" onClick={toggleMode} className="text-pink-500 hover:text-pink-600 font-medium">
                  注册
                </button>
              </span>
            ) : (
              <span>
                已有账户？{' '}
                <button type="button" onClick={toggleMode} className="text-pink-500 hover:text-pink-600 font-medium">
                  登录
                </button>
              </span>
            )}
          </div>
        </form>

        {/* 了解产品（SP-11 公开介绍页入口） */}
        <div className="text-center mt-5">
          <Link to="/intro" className="text-xs text-gray-400 hover:text-macaron-blue-deep transition-colors">
            了解产品 →
          </Link>
        </div>
      </div>
    </div>
  )
}
