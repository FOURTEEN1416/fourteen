import { useState, useEffect, useCallback } from 'react'
import { Wifi } from 'lucide-react'
import { tools as fetchTools, toggleTool } from '../api/system'

const TOOL_DESCRIPTIONS: Record<string, string> = {
  'weather': '获取城市天气信息',
  'calendar': '设置和管理日历日程',
  'search': '联网搜索验证事实',
  'memory': '长期记忆查询',
  'character_card': '角色信息管理',
  'image_gen': 'AI 图片创作',
  'web_summary': 'URL 内容摘要',
  'calculator': '数学计算与公式',
  'scheduler': '定时提醒与调度',
}

const TOOL_NAMES = Object.keys(TOOL_DESCRIPTIONS)

export default function ToolsDashboard() {
  const [tools, setTools] = useState<Record<string, boolean>>({})
  const [loading, setLoading] = useState(true)
  const [toggling, setToggling] = useState<string | null>(null)

  const doFetch = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetchTools()
      const names: string[] = res.data?.tools ?? []
      const enabledMap: Record<string, boolean> = {}
      for (const key of TOOL_NAMES) {
        enabledMap[key] = names.includes(key)
      }
      setTools(enabledMap)
    } catch {
      setTools({})
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    doFetch()
  }, [doFetch])

  const handleToggle = async (name: string, enabled: boolean) => {
    setToggling(name)
    try {
      await toggleTool(name, enabled)
      setTools((prev) => ({ ...prev, [name]: enabled }))
    } catch {
      // revert on error
    } finally {
      setToggling(null)
    }
  }

  const onlineCount = Object.values(tools).filter(Boolean).length
  const totalCount = TOOL_NAMES.length

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h2 className="text-lg font-semibold text-gray-700">工具仪表盘</h2>
        <p className="mt-0.5 text-xs text-gray-400">
          查看内置工具与 MCP 服务的运行状态
        </p>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-500 border-t-transparent" />
        </div>
      ) : (
        <section>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700">
            <Wifi className="h-4 w-4 text-gray-400" />
            内置工具
            <span className="ml-auto rounded-full bg-green-50 px-2 py-0.5 text-[10px] font-medium text-green-600">
              {onlineCount}/{totalCount} 可用
            </span>
          </h3>
          <div className="rounded-xl border border-gray-200 bg-white/60 p-4 space-y-2">
            {TOOL_NAMES.map((name) => {
              const enabled = tools[name] ?? false
              const isToggling = toggling === name
              return (
                <div
                  key={name}
                  className="flex items-center gap-3 rounded-lg border border-gray-100 bg-white/40 px-4 py-3"
                >
                  <span
                    className={`inline-block h-2 w-2 rounded-full ${
                      enabled ? 'bg-green-400' : 'bg-gray-200'
                    }`}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-gray-700">{name}</p>
                    <p className="text-xs text-gray-400">{TOOL_DESCRIPTIONS[name]}</p>
                  </div>
                  <button
                    onClick={() => handleToggle(name, !enabled)}
                    disabled={isToggling}
                    className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                      isToggling ? 'opacity-50' : ''
                    } ${enabled ? 'bg-primary-400' : 'bg-gray-200'}`}
                  >
                    <span
                      className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow-sm transition-transform ${
                        enabled ? 'translate-x-[18px]' : 'translate-x-[2px]'
                      }`}
                    />
                  </button>
                </div>
              )
            })}
          </div>
        </section>
      )}
    </div>
  )
}
