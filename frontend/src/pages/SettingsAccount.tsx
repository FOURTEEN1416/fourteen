import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Download, ShieldAlert, ShieldOff, UserX } from 'lucide-react'
import { consent } from '../api/auth'
import {
  deleteAccount,
  exportAccountManifest,
  exportAllChats,
  getConsentStatus,
  isDeleteAccepted,
  withdrawConsent,
} from '../api/selfservice'
import type { AccountManifest, ConsentState } from '../api/selfservice'
import { datedFilename, downloadJson } from '../utils/download'
import { AGREEMENT_VERSION } from '../constants/agreement'
import { useAuth } from '../hooks/useAuth'
import { useErrorStore } from '../store/errorStore'

// ── W17 自服务面契约（逐项对照 api/routers/auth_routes.py + api/lifecycle.py）──
//
// GET  /api/auth/consent/status     → {status: granted|missing|withdrawn|outdated, agreement_version}
// POST /api/auth/consent/withdraw   → {status}（服务端现值，客户端不自行推断）
// POST /api/auth/account/delete     → {job_id, user_id, status: queued|purging|completed|failed, completed, steps}
// GET  /api/auth/account/export     → {user_id, session_keys, categories{9 项}}
// GET  /api/auth/account/export/chats → {messages, next_before_id}（id 游标）
//
// 三条不可让步的口径：
// ① 判定一律业务回执——HTTP 200 而 status 不是预期态即失败（W11 纪律）；
// ② 注销回执**永远不宣称「已删除」**——后端只做冻结 + 异步清除，文案止于「已受理」；
// ③ 撤回同意会让四通道 fail-closed（api/consent.py 分层强制），故必须同时给出「重新同意」入口，
//    否则用户把自己锁在外面却没有恢复路径（协议门只在登录/刷新时弹）。

const CONSENT_LABELS: Record<ConsentState, string> = {
  granted: '已同意',
  missing: '尚未同意',
  withdrawn: '已撤回同意',
  outdated: '协议版本有更新，需重新同意',
}

/** 清单类别 → 中文标签（顺序即展示顺序；后端缺字段显示 0，即「没有这类数据」） */
const CATEGORY_ROWS: [keyof AccountManifest['categories'], string][] = [
  ['chats', '对话记录'],
  ['facts', '记忆事实'],
  ['reflections', '反思'],
  ['reminders', '提醒'],
  ['profile', '画像条目'],
  ['diary', '日记'],
  ['consents', '协议记录'],
  ['wechat_bindings', '微信绑定'],
  ['wechat_channels', '微信通道'],
]

function Spinner() {
  return (
    <div className="flex items-center justify-center py-8">
      <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-500 border-t-transparent" />
    </div>
  )
}

export default function SettingsAccount() {
  const navigate = useNavigate()
  const { logout } = useAuth()

  const [consentState, setConsentState] = useState<ConsentState | null>(null)
  const [agreementVersion, setAgreementVersion] = useState('')
  const [consentLoading, setConsentLoading] = useState(true)
  const [consentError, setConsentError] = useState<string | null>(null)
  const [withdrawing, setWithdrawing] = useState(false)
  const [reConsenting, setReConsenting] = useState(false)

  const [manifest, setManifest] = useState<AccountManifest | null>(null)
  const [manifestLoading, setManifestLoading] = useState(true)
  const [manifestError, setManifestError] = useState<string | null>(null)
  const [exportingData, setExportingData] = useState(false)
  const [exportingChats, setExportingChats] = useState(false)

  const [confirmText, setConfirmText] = useState('')
  const [deleting, setDeleting] = useState(false)

  const loadConsent = useCallback(async () => {
    setConsentLoading(true)
    setConsentError(null)
    try {
      const res = await getConsentStatus()
      setConsentState(res?.status ?? null)
      setAgreementVersion(res?.agreement_version ?? '')
    } catch (err: unknown) {
      setConsentState(null)
      setConsentError(err instanceof Error ? err.message : '无法加载同意状态')
    } finally {
      setConsentLoading(false)
    }
  }, [])

  const loadManifest = useCallback(async () => {
    setManifestLoading(true)
    setManifestError(null)
    try {
      setManifest(await exportAccountManifest())
    } catch {
      // 加载失败 ≠ 「0 条数据」：明确报错并给重试，不拿 0 冒充
      setManifest(null)
      setManifestError('无法加载账号数据')
    } finally {
      setManifestLoading(false)
    }
  }, [])

  /* eslint-disable react-hooks/set-state-in-effect -- 挂载时拉取两份独立状态 */
  useEffect(() => {
    void loadConsent()
    void loadManifest()
  }, [loadConsent, loadManifest])
  /* eslint-enable react-hooks/set-state-in-effect */

  const handleWithdraw = async () => {
    setWithdrawing(true)
    try {
      const res = await withdrawConsent()
      if (res?.status !== 'withdrawn') {
        // 假成功守卫：HTTP 成功但服务端未撤回 → 状态不动，只给 warning
        useErrorStore.getState().addToast({
          type: 'warning',
          message: `撤回未生效（后端回执状态 ${res?.status ?? '未知'}），当前设置保持不变`,
        })
        return
      }
      setConsentState('withdrawn')
      useErrorStore.getState().addToast({
        type: 'success',
        message: '已撤回同意，聊天与主动消息通道已停止；如需恢复请点「重新同意」',
      })
    } catch (err: unknown) {
      useErrorStore.getState().addToast({
        type: 'error',
        message: err instanceof Error ? err.message : '撤回同意失败，请重试',
      })
    } finally {
      setWithdrawing(false)
    }
  }

  const handleReConsent = async () => {
    setReConsenting(true)
    try {
      await consent(AGREEMENT_VERSION)
      await loadConsent()
      useErrorStore.getState().addToast({ type: 'success', message: '已同意当前版本协议' })
    } catch (err: unknown) {
      useErrorStore.getState().addToast({
        type: 'error',
        message: err instanceof Error ? err.message : '同意记录失败，请重试',
      })
    } finally {
      setReConsenting(false)
    }
  }

  const handleExportData = async () => {
    setExportingData(true)
    try {
      const payload = await exportAccountManifest()
      downloadJson(datedFilename('account-export', 'json'), payload)
      useErrorStore.getState().addToast({ type: 'success', message: '账号数据已导出为文件' })
    } catch (err: unknown) {
      useErrorStore.getState().addToast({
        type: 'error',
        message: err instanceof Error ? err.message : '导出失败，请重试',
      })
    } finally {
      setExportingData(false)
    }
  }

  const handleExportChats = async () => {
    setExportingChats(true)
    try {
      const payload = await exportAllChats()
      downloadJson(datedFilename('chats-export', 'json'), payload)
      useErrorStore.getState().addToast({
        type: 'success',
        message: `聊天记录已导出（${payload.total_messages} 条）`,
      })
    } catch (err: unknown) {
      useErrorStore.getState().addToast({
        type: 'error',
        message: err instanceof Error ? err.message : '导出失败，请重试',
      })
    } finally {
      setExportingChats(false)
    }
  }

  const handleDelete = async () => {
    setDeleting(true)
    try {
      const receipt = await deleteAccount()
      if (!isDeleteAccepted(receipt)) {
        useErrorStore.getState().addToast({
          type: 'error',
          message: `注销申请未被受理（回执状态 ${receipt?.status ?? '未知'}），账号保持不变`,
        })
        return
      }
      // 受理即登出：logout() 内部清认证态 + 清私人查询缓存（不得跨账号驻留）
      await logout()
      navigate('/login')
      useErrorStore.getState().addToast({
        type: 'success',
        message: '注销申请已受理，数据清除正在后台进行',
      })
    } catch (err: unknown) {
      useErrorStore.getState().addToast({
        type: 'error',
        message: err instanceof Error ? err.message : '注销失败，请重试',
      })
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* ── 协议与同意 ── */}
      <section>
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700">
          <ShieldAlert className="h-4 w-4 text-macaron-blue-deep" />
          协议与同意
        </h3>
        <div className="rounded-xl border border-blue-200/40 bg-white/60 p-4 space-y-3">
          {consentLoading ? (
            <Spinner />
          ) : consentError ? (
            <div className="space-y-3 text-center">
              <p className="text-sm text-red-600">{consentError}</p>
              <button
                onClick={loadConsent}
                className="rounded-lg border border-red-200 bg-white/80 px-4 py-1.5 text-xs text-red-600 hover:bg-white transition-colors"
              >
                重试
              </button>
            </div>
          ) : (
            <>
              <p className="text-sm text-gray-700">
                {consentState ? `${CONSENT_LABELS[consentState]} v${agreementVersion}` : '状态未知'}
              </p>
              {consentState === 'withdrawn' && (
                <p className="text-xs text-amber-600">
                  撤回后聊天、语音、微信与后台主动消息均已停止，重新同意即恢复。
                </p>
              )}
              <div className="flex flex-wrap gap-2">
                {consentState === 'granted' && (
                  <button
                    onClick={handleWithdraw}
                    disabled={withdrawing}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-1.5 text-xs text-amber-700 hover:bg-amber-100/60 transition-colors disabled:opacity-50"
                  >
                    <ShieldOff className="h-3.5 w-3.5" />
                    {withdrawing ? '撤回中…' : '撤回同意'}
                  </button>
                )}
                {consentState && consentState !== 'granted' && (
                  <button
                    onClick={handleReConsent}
                    disabled={reConsenting}
                    className="rounded-lg border border-macaron-blue/40 bg-macaron-blue-light/30 px-3 py-1.5 text-xs text-macaron-blue-deep hover:bg-macaron-blue-light/50 transition-colors disabled:opacity-50"
                  >
                    {reConsenting ? '提交中…' : '重新同意'}
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      </section>

      {/* ── 我的数据 ── */}
      <section>
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700">
          <Download className="h-4 w-4 text-macaron-mint-deeper" />
          我的数据
        </h3>
        <div className="rounded-xl border border-green-200/40 bg-white/60 p-4 space-y-4">
          {manifestLoading ? (
            <Spinner />
          ) : manifestError ? (
            <div className="space-y-3 text-center">
              <p className="text-sm text-red-600">{manifestError}</p>
              <button
                onClick={loadManifest}
                className="rounded-lg border border-red-200 bg-white/80 px-4 py-1.5 text-xs text-red-600 hover:bg-white transition-colors"
              >
                重试
              </button>
            </div>
          ) : manifest ? (
            <>
              <div className="grid grid-cols-3 gap-3">
                {CATEGORY_ROWS.map(([key, label]) => (
                  <div key={key} className="rounded-lg border border-gray-100 bg-white/40 p-3 text-center">
                    <p className="text-xl font-bold text-gray-700">{manifest.categories?.[key] ?? 0}</p>
                    <p className="text-[10px] text-gray-400 mt-0.5">{label}</p>
                  </div>
                ))}
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={handleExportData}
                  disabled={exportingData}
                  className="rounded-lg border border-macaron-blue/40 bg-macaron-blue-light/30 px-3 py-1.5 text-xs text-macaron-blue-deep hover:bg-macaron-blue-light/50 transition-colors disabled:opacity-50"
                >
                  {exportingData ? '导出中…' : '导出我的数据'}
                </button>
                <button
                  onClick={handleExportChats}
                  disabled={exportingChats}
                  className="rounded-lg border border-macaron-mint-deeper/30 bg-white/60 px-3 py-1.5 text-xs text-macaron-mint-deeper hover:bg-macaron-mint/20 transition-colors disabled:opacity-50"
                >
                  {exportingChats ? '导出中…' : '导出聊天记录'}
                </button>
              </div>
              <p className="text-[11px] text-gray-400">
                导出内容为本人全部数据的 JSON 文件（文件名带导出日期）；聊天记录按会话逐条游标取回，按写入顺序排列。
              </p>
            </>
          ) : null}
        </div>
      </section>

      {/* ── 注销账号 ── */}
      <section>
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700">
          <UserX className="h-4 w-4 text-red-400" />
          注销账号
        </h3>
        <div className="rounded-xl border border-red-200/40 bg-red-50/30 p-4 space-y-3">
          <p className="text-xs text-gray-600 leading-relaxed">
            注销会立即冻结账号并停止全部服务，对话记录、记忆、画像、日记与绑定关系将在后台清除。
            <span className="font-medium text-red-600">此操作不可撤销。</span>
          </p>
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex-1 min-w-[12rem]">
              <label htmlFor="account-delete-confirm" className="block text-[11px] text-gray-500 mb-1">
                请输入「注销」以确认
              </label>
              <input
                id="account-delete-confirm"
                data-testid="delete-confirm-input"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder="注销"
                className="w-full rounded-lg border border-red-200 bg-white/80 px-3 py-1.5 text-sm outline-none focus:border-red-300 transition-colors"
              />
            </div>
            <button
              onClick={handleDelete}
              disabled={confirmText.trim() !== '注销' || deleting}
              className="rounded-lg border border-red-300 bg-red-500/90 px-4 py-1.5 text-xs font-medium text-white hover:bg-red-600 transition-colors disabled:cursor-not-allowed disabled:opacity-40"
            >
              {deleting ? '提交中…' : '注销账号'}
            </button>
          </div>
        </div>
      </section>
    </div>
  )
}
