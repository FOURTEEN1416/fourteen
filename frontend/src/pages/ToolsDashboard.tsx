import { useState, useEffect, useCallback } from 'react'
import { Wifi, AlertCircle, Wrench } from 'lucide-react'
import { tools as fetchTools, toolsHealth, toggleTool } from '../api/system'

interface ToolHealth {
  available: boolean
  error?: string
  note?: string
  config_hint?: string
  features?: Record<string, boolean>
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
}

const TOOL_NAMES = Object.keys(TOOL_DESCRIPTIONS)

export default function ToolsDashboard() {
  const [tools, setTools] = useState<Record<string, boolean>>({})
  const [health, setHealth] = useState<Record<string, ToolHealth>>({})
  const [loading, setLoading] = useState(true)
  const [toggling, setToggling] = useState<string | null>(null)

  const doFetch = useCallback(async () => {
    setLoading(true)
    try {
      const [toolsRes, healthRes] = await Promise.all([
        fetchTools(),
        toolsHealth().catch(() => ({ data: { tools: {} } })),
      ])
      const enabledNames: string[] = toolsRes.data?.tools ?? []
      const enabledMap: Record<string, boolean> = {}
      for (const key of TOOL_NAMES) {
        enabledMap[key] = enabledNames.includes(key)
      }
      setTools(enabledMap)
      setHealth(healthRes.data?.tools ?? {})
    } catch {
      setTools({})
      setHealth({})
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
      setTools((prev) => ({ ...prev, [name]: enabled }))
    } catch {
      // revert on error is implicit since we only update on success
    } finally {
      setToggling(null)
    }
  }

  const onlineCount = TOOL_NAMES.filter(
    (name) => health[name]?.available ?? true
  ).length
  const totalCount = TOOL_NAMES.length
  const allHealthy = onlineCount === totalCount

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h2 className="text-lg font-semibold text-gray-700">工具仪表盘</h2>
        <p className="mt-0.5 text-xs text-gray-400">
          查看内置工具的真实运行状态与可用性
        </p>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12" role="status" aria-label="加载中">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-500 border-t-transparent" />
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
          <div className="rounded-xl border border-gray-200 bg-white/60 p-4 space-y-2">
            {TOOL_NAMES.map((name) => {
              const enabled = tools[name] ?? false
              const toolHealth = health[name]
              const available = toolHealth?.available ?? true
              const isToggling = toggling === name
              const errorText = toolHealth?.error
              const hintText = toolHealth?.config_hint || toolHealth?.note

              return (
                <div
                  key={name}
                  className="flex flex-col gap-2 rounded-lg border border-gray-100 bg-white/40 px-4 py-3"
                >
                  <div className="flex items-center gap-3">
                    <span
                      className={`inline-block h-2 w-2 rounded-full ${
                        available ? 'bg-green-400' : 'bg-red-400'
                      }`}
                      title={available ? '可用' : '不可用'}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-gray-700">{name}</p>
                      <p className="text-xs text-gray-400">
                        {TOOL_DESCRIPTIONS[name]}
                      </p>
                    </div>
                    <button
                      onClick={() => handleToggle(name, !enabled)}
                      disabled={isToggling}
                      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                        isToggling ? 'opacity-50' : ''
                      } ${enabled ? 'bg-primary-500' : 'bg-gray-300'}`}
                      aria-label={enabled ? `禁用 ${name}` : `启用 ${name}`}
                    >
                      <span
                        className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow-sm transition-transform ${
                          enabled ? 'translate-x-[18px]' : 'translate-x-[2px]'
                        }`}
                      />
                    </button>
                  </div>

                  {!available && errorText && (
                    <div className="flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">
                      <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      <span className="flex-1">{errorText}</span>
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
