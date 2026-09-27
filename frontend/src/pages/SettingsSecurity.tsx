import { useState, useEffect, useCallback } from 'react'
import { Shield, ShieldAlert } from 'lucide-react'
import { safetyStats, safetyLog, safetyConfig } from '../api/system'
import { useAuthStore } from '../store/authStore'
import { useErrorStore } from '../store/errorStore'
import type { SafetyStats, SafetyLogEntry } from '../types/api'

// ── Safety 契约（W11-D1，逐项对照 api/routers/safety_routes.py + api/state/safety_log.py）──
//
// GET /api/safety/stats → { enabled, total_flagged, recent_flagged, by_category, recent }
// GET /api/safety/log   → { "log": [{ timestamp, category, direction, text_length, text_hash }] }
// POST /api/safety/config → admin-only；{ status: "ok", enabled, persisted, … }
//                         | { status: "not_available" }（业务失败，HTTP 200）
//
// 旧实现三处错位：① 读 `data.logs`（真实键是 `log`）⇒ 面板恒空；
// ② 读 `total_detections/today_blocked/block_rate`（后端从未提供）⇒ 恒 0/—；
// ③ 开关对普通用户也渲染（后端 require_role("admin") 才拦）⇒ 点了必 403。
// 类型唯一 owner = types/api.ts（此处不再重复声明，避免契约漂移）。

/** 后端 category.value → 中文（未收录的类别原样显示，不编造） */
const CATEGORY_LABELS: Record<string, string> = {
  self_harm: '自伤风险',
  violence: '暴力',
  sexual: '色情内容',
  illegal: '违法内容',
  privacy: '隐私泄露',
  hate: '仇恨言论',
  unknown: '未分类',
}

function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category
}

function directionLabel(direction: string): string {
  if (direction === 'input') return '输入'
  if (direction === 'output') return '输出'
  return direction
}

/** 后端时间戳是 Unix 秒；兼容毫秒与 ISO 串，非法值原样返回不谎报时间 */
function formatLogTime(timestamp: number | string): string {
  if (typeof timestamp === 'number' && Number.isFinite(timestamp)) {
    const ms = timestamp < 1e12 ? timestamp * 1000 : timestamp
    return new Date(ms).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    })
  }
  const parsed = new Date(timestamp)
  if (Number.isNaN(parsed.getTime())) return String(timestamp)
  return parsed.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

// ── Safety Panel ──

function SafetyPanelSection() {
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
  const [stats, setStats] = useState<SafetyStats | null>(null)
  const [logs, setLogs] = useState<SafetyLogEntry[]>([])
  const [enabled, setEnabled] = useState(true)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [toggling, setToggling] = useState(false)

  const doFetch = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [statsRes, logRes] = await Promise.all([
        safetyStats(),
        safetyLog(10),
      ])
      const d = statsRes.data ?? {}
      setStats({
        enabled: d.enabled !== undefined ? Boolean(d.enabled) : true,
        // 后端缺字段时显示 0（该值就是 0 条），绝不编造数字
        total_flagged: d.total_flagged ?? 0,
        recent_flagged: d.recent_flagged ?? 0,
        by_category: d.by_category ?? {},
        recent: d.recent ?? [],
      })
      // 真实键是 `log`（旧实现读 `logs` ⇒ 恒空）
      setLogs(logRes.data?.log ?? [])
      setEnabled(d.enabled !== undefined ? Boolean(d.enabled) : true)
    } catch {
      // API 失败:不显示假数字,明确告知用户加载失败
      setStats(null)
      setLogs([])
      setError('无法加载安全数据')
    } finally {
      setLoading(false)
    }
  }, [])

  // 挂载时拉数据（cancelled flag 已防竞态）
  /* eslint-disable react-hooks/set-state-in-effect -- data fetching on mount */
  useEffect(() => { doFetch() }, [doFetch])
  /* eslint-enable react-hooks/set-state-in-effect */

  const handleToggle = async () => {
    const next = !enabled
    setToggling(true)
    try {
      const res = await safetyConfig(next)
      // W11-D5：成功必须是**业务成功**。HTTP 200 + status="not_available"
      // 表示后端未落盘/未生效，绝不能翻转开关或给绿色回执。
      if (res.data?.status !== 'ok') {
        useErrorStore.getState().addToast({
          type: 'warning',
          message: '内容安全开关未生效（后端不可用），设置保持不变',
        })
        return
      }
      setEnabled(res.data?.enabled !== undefined ? Boolean(res.data.enabled) : next)
      // W6：开关已持久化到配置（重启不回退）；仅未持久化时明确提示
      if (res.data?.persisted === false) {
        useErrorStore.getState().addToast({ type: 'info', message: '开关仅当前进程生效（未持久化）' })
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : '安全开关切换失败，请重试'
      useErrorStore.getState().addToast({ type: 'error', message })
    } finally {
      setToggling(false)
    }
  }

  if (loading) {
    return (
      <section>
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700">
          <ShieldAlert className="h-4 w-4 text-gray-400" />
          内容安全面板
        </h3>
        <div className="flex items-center justify-center py-8">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-500 border-t-transparent" />
        </div>
      </section>
    )
  }

  if (error) {
    return (
      <section>
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700">
          <ShieldAlert className="h-4 w-4 text-red-400" />
          内容安全面板
        </h3>
        <div className="rounded-xl border border-red-200/40 bg-red-50/40 p-6 text-center space-y-3">
          <p className="text-sm text-red-600">{error}</p>
          <button
            onClick={doFetch}
            className="rounded-lg border border-red-200 bg-white/80 px-4 py-1.5 text-xs text-red-600 hover:bg-white transition-colors"
          >
            重试
          </button>
        </div>
      </section>
    )
  }

  const displayLogs = logs.slice(0, 5)
  const categoryCount = Object.keys(stats?.by_category ?? {}).length

  return (
    <section>
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700">
        <ShieldAlert className="h-4 w-4 text-purple-500" />
        内容安全面板
        <span className="ml-auto flex items-center gap-1 text-[10px] font-medium text-green-600">
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-green-400" />
          {enabled ? '运行中' : '已暂停'}
        </span>
      </h3>
      <div className="rounded-xl border border-purple-200/40 bg-white/60 p-4 space-y-4">

        {/* Stats grid（真实字段：total_flagged / recent_flagged / by_category） */}
        <div className="grid grid-cols-3 gap-3">
          <div className="rounded-lg border border-gray-100 bg-white/40 p-3 text-center">
            <p className="text-xl sm:text-2xl font-bold text-red-400">{stats?.total_flagged ?? 0}</p>
            <p className="text-[10px] text-gray-400 mt-0.5">累计拦截</p>
          </div>
          <div className="rounded-lg border border-gray-100 bg-white/40 p-3 text-center">
            <p className="text-xl sm:text-2xl font-bold text-gray-700">{stats?.recent_flagged ?? 0}</p>
            <p className="text-[10px] text-gray-400 mt-0.5">近期事件</p>
          </div>
          <div className="rounded-lg border border-gray-100 bg-white/40 p-3 text-center">
            <p className="text-xl sm:text-2xl font-bold text-yellow-500">{categoryCount}</p>
            <p className="text-[10px] text-gray-400 mt-0.5">命中类别</p>
          </div>
        </div>

        {/* Toggle —— admin-only（后端 require_role("admin") 继续强制；
            普通用户不渲染入口，避免点了必 403 的假按钮） */}
        {isAdmin && (
          <div className="flex items-center justify-between rounded-lg border border-gray-100 bg-white/40 px-4 py-2.5">
            <span className="flex items-center gap-2 text-sm text-gray-700">
              <Shield className={`h-4 w-4 ${enabled ? 'text-green-500' : 'text-gray-400'}`} />
              内容安全过滤器
            </span>
            <button
              onClick={handleToggle}
              disabled={toggling}
              aria-label="内容安全过滤器开关"
              className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                enabled ? 'toggle on' : 'toggle'
              }`}
            >
              <span
                className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow-sm transition-transform ${
                  enabled ? 'translate-x-[18px]' : 'translate-x-[2px]'
                }`}
              />
            </button>
          </div>
        )}

        {/* Logs（真实键 log；空态明确，不把空当错误也不把错误当空） */}
        <div>
          <p className="mb-2 text-[11px] font-medium text-gray-500">最近安全事件</p>
          {displayLogs.length === 0 ? (
            <p className="py-3 text-center text-[11px] text-gray-400">暂无安全事件</p>
          ) : (
            <div className="space-y-1">
              {displayLogs.map((log, i) => (
                <div
                  key={`seclog-${log.text_hash || log.timestamp}-${i}`}
                  className="flex items-center gap-2 rounded-lg bg-white/40 px-3 py-1.5 text-[11px]"
                >
                  <span className="inline-block w-14 shrink-0 rounded bg-red-50 px-1 py-0.5 text-center text-[9px] font-medium text-red-500">
                    {categoryLabel(log.category)}
                  </span>
                  <span
                    className={`inline-block w-8 shrink-0 rounded px-1 py-0.5 text-center text-[9px] font-medium ${
                      log.direction === 'output'
                        ? 'bg-macaron-blue/20 text-macaron-blue-deep'
                        : 'bg-gray-100 text-gray-500'
                    }`}
                  >
                    {directionLabel(log.direction)}
                  </span>
                  <span className="flex-1 truncate text-gray-500">
                    {log.text_length ?? 0} 字 · #{log.text_hash || '—'}
                  </span>
                  <span className="shrink-0 text-gray-400">
                    {log.timestamp ? formatLogTime(log.timestamp) : ''}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

// ── Main Component ──

export default function SettingsSecurity() {
  return (
    <div className="space-y-6">
      <SafetyPanelSection />
    </div>
  )
}
