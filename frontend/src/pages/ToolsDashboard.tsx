import { useState, useEffect, useCallback } from 'react'
import { Wifi, AlertCircle, Wrench, PauseCircle } from 'lucide-react'
import { tools as fetchTools, toolsHealth, toggleTool } from '../api/system'

interface ToolHealth {
  available: boolean
  error?: string
  note?: string
  config_hint?: string
  features?: Record<string, boolean>
}

type ToolStatus = 'enabled' | 'disabled' | 'unavailable'

interface ToolInventoryEntry {
  name: string
  status: ToolStatus
  reason?: string
  permission_level?: string | null
}

const TOOL_DESCRIPTIONS: Record<string, string> = {
  weather: '获取城市天气信息',
  calendar: '设置和管理日历日程',
  search: '联网搜索验证事实',
  memory: '长期记忆查询',
  character_card: '角色信息管理',
  image_gen: 'AI 图片创作',
  web_summary: 'URL 内容摘要',
  calculator: '数学计算与公式',
  scheduler: '定时提醒与调度',
  set_reminder: '设置定时提醒',
  query_reminders: '查询已有提醒',
  time_awareness: '时间感知与日程认知',
}

const STATUS_LABEL: Record<ToolStatus, string> = {
  enabled: '启用',
  disabled: '已禁用',
  unavailable: '不可用',
}

const STATUS_DOT: Record<ToolStatus, string> = {
  enabled: 'bg-green-400',
  disabled: 'bg-gray-300',
  unavailable: 'bg-red-400',
}

export default function ToolsDashboard() {
  const [inventory, setInventory] = useState<ToolInventoryEntry[]>([])
  const [health, setHealth] = useState<Record<string, ToolHealth>>({})
  const [summary, setSummary] = useState({ total: 0, online: 0 })
  const [loading, setLoading] = useState(true)
  const [toggling, setToggling] = useState<string | null>(null)

  const doFetch = useCallback(async () => {
    setLoading(true)
    try {
      const [toolsRes, healthRes] = await Promise.all([
        fetchTools(),
        toolsHealth().catch(() => ({ data: { tools: {}, total: 0, online: 0 } })),
      ])
      // W6：统一库存（enabled/disabled/unavailable 全量可见，禁用后刷新不丢入口）
      const inv: ToolInventoryEntry[] = toolsRes.data?.inventory ?? []
      if (inv.length > 0) {
        setInventory(inv)
      } else {
        // 兼容旧后端：只有启用名列表
        setInventory(
          (toolsRes.data?.tools ?? []).map((name: string) => ({
            name,
            status: 'enabled' as ToolStatus,
          })),
        )
      }
      setHealth(healthRes.data?.tools ?? {})
      setSummary({
        total: healthRes.data?.total ?? 0,
        online: healthRes.data?.online ?? 0,
      })
    } catch {
      setInventory([])
      setHealth({})
      setSummary({ total: 0, online: 0 })
    } finally {
      setLoading(false)
    }
  }, [])

  /* eslint-disable react-hooks/set-state-in-effect -- data fetching on mount */
  useEffect(() => {
    doFetch()
  }, [doFetch])
  /* eslint-enable react-hooks/set-state-in-effect */

  const handleToggle = async (name: string, enabled: boolean) => {
    setToggling(name)
    try {
      await toggleTool(name, enabled)
      setInventory((prev) =>
        prev.map((t) => (t.name === name ? { ...t, status: enabled ? 'enabled' : 'disabled' } : t)),
      )
    } catch {
      // revert on error is implicit since we only update on success
    } finally {
      setToggling(null)
    }
  }

  const onlineCount = summary.online
  const totalCount = summary.total
  const allHealthy = totalCount > 0 && onlineCount === totalCount

  return (
    <div className="space-y-6">
      <p className="text-xs text-gray-400">
        查看内置工具的运行状态、可用性与启停开关（开关跨重启持久生效）
      </p>

      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2" role="status" aria-label="加载中">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-[4.25rem] rounded-lg animate-pulse bg-white/40" />
          ))}
        </div>
      ) : (
        <section>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700">
            <Wifi className="h-4 w-4 text-gray-400" />
            内置工具
            <span
              className={`ml-auto rounded-full px-2 py-0.5 text-[10px] font-medium ${
                allHealthy
                  ? 'bg-green-50 text-green-600'
                  : 'bg-amber-50 text-amber-600'
              }`}
            >
              {onlineCount}/{totalCount} 可用
            </span>
          </h3>
          <div className="rounded-xl border border-gray-200 bg-white/60 p-4 grid grid-cols-1 sm:grid-cols-2 gap-2 content-start">
            {inventory.map((entry) => {
              const toolHealth = health[entry.name]
              const available = toolHealth?.available ?? entry.status !== 'unavailable'
              const isToggling = toggling === entry.name
              const errorText = toolHealth?.error || entry.reason
              const hintText = toolHealth?.config_hint || toolHealth?.note
              const toggleable = entry.status !== 'unavailable'

              return (
                <div
                  key={entry.name}
                  className="flex flex-col gap-2 rounded-lg border border-gray-100 bg-white/40 px-4 py-3"
                >
                  <div className="flex items-center gap-3">
                    <span
                      className={`inline-block h-2 w-2 rounded-full ${STATUS_DOT[entry.status]}`}
                      title={STATUS_LABEL[entry.status]}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-gray-700">
                        {entry.name}
                        <span className="ml-2 text-[10px] text-gray-400">
                          {STATUS_LABEL[entry.status]}
                        </span>
                      </p>
                      <p className="text-xs text-gray-400">
                        {TOOL_DESCRIPTIONS[entry.name] || entry.name}
                      </p>
                    </div>
                    {toggleable ? (
                      <button
                        onClick={() => handleToggle(entry.name, entry.status !== 'enabled')}
                        disabled={isToggling}
                        className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                          isToggling ? 'opacity-50' : ''
                        } ${entry.status === 'enabled' ? 'bg-primary-500' : 'bg-gray-300'}`}
                        aria-label={entry.status === 'enabled' ? `禁用 ${entry.name}` : `启用 ${entry.name}`}
                      >
                        <span
                          className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow-sm transition-transform ${
                            entry.status === 'enabled' ? 'translate-x-[18px]' : 'translate-x-[2px]'
                          }`}
                        />
                      </button>
                    ) : (
                      <PauseCircle className="h-4 w-4 text-gray-300" aria-hidden />
                    )}
                  </div>

                  {!available && errorText && (
                    <div className="flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">
                      <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      <span className="flex-1">{errorText}</span>
                    </div>
                  )}

                  {available && entry.status === 'disabled' && entry.reason && (
                    <div className="flex items-start gap-2 rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-500">
                      <PauseCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      <span className="flex-1">{entry.reason}</span>
                    </div>
                  )}

                  {hintText && (
                    <div className="flex items-start gap-2 rounded-lg bg-blue-50 px-3 py-2 text-xs text-blue-600">
                      <Wrench className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      <span className="flex-1">{hintText}</span>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </section>
      )}
    </div>
  )
}
