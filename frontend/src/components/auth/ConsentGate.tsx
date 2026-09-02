/**
 * 协议同意门（W2-CONSENT 使用即同意）
 *
 * 登录/注册后若后端返回 needs_consent=true，全屏展示《用户协议与隐私声明》，
 * 用户点击"同意并继续"后调 POST /api/auth/consent 落库，之后才放行进入控制台。
 * 协议版本升级时后端重新返回 needs_consent=true，实现"版本变更重新同意"。
 */
import { useState } from 'react'
import {
  AGREEMENT_TITLE,
  AGREEMENT_TEXT,
  AGREEMENT_VERSION,
} from '../../constants/agreement'
import { useAuthStore } from '../../store/authStore'
import { useAuth } from '../../hooks/useAuth'

export default function ConsentGate() {
  const { isAuthenticated, isInitialized, needsConsent } = useAuthStore()
  const { agreeConsent, logout } = useAuth()
  const [agreeing, setAgreeing] = useState(false)
  const [error, setError] = useState('')

  if (!isAuthenticated || !isInitialized || !needsConsent) return null

  const handleAgree = async () => {
    setAgreeing(true)
    setError('')
    try {
      await agreeConsent()
    } catch {
      setError('提交失败，请重试')
    } finally {
      setAgreeing(false)
    }
  }

  const handleDisagree = () => {
    // 不同意 → 视为放弃使用，退出登录
    void logout()
  }

  return (
    <div
      data-testid="consent-gate"
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
    >
      <div className="w-full max-w-lg glass-card rounded-2xl flex flex-col max-h-[85vh]">
        {/* 标题 */}
        <div className="px-6 pt-6 pb-4 text-center border-b border-gray-200/40">
          <h2 className="text-xl font-bold text-gray-700">{AGREEMENT_TITLE}</h2>
          <p className="text-xs text-gray-400 mt-1">
            版本 v{AGREEMENT_VERSION} · 使用本系统即表示同意本协议
          </p>
        </div>

        {/* 协议正文（可滚动） */}
        <div
          data-testid="agreement-body"
          className="flex-1 overflow-y-auto px-6 py-4 text-sm text-gray-600 leading-relaxed whitespace-pre-wrap"
        >
          {AGREEMENT_TEXT}
        </div>

        {/* 错误提示 */}
        {error && (
          <div className="px-6 pb-2">
            <div className="text-sm text-red-500 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
              {error}
            </div>
          </div>
        )}

        {/* 操作区 */}
        <div className="px-6 py-4 border-t border-gray-200/40 flex flex-col gap-2">
          <button
            type="button"
            onClick={handleAgree}
            disabled={agreeing}
            className="btn-macaron w-full rounded-lg py-2.5 text-sm font-medium disabled:opacity-50"
          >
            {agreeing ? '提交中...' : '我已阅读并同意'}
          </button>
          <button
            type="button"
            onClick={handleDisagree}
            disabled={agreeing}
            className="text-xs text-gray-400 hover:text-gray-600 py-1 disabled:opacity-50"
          >
            不同意并退出登录
          </button>
        </div>
      </div>
    </div>
  )
}
