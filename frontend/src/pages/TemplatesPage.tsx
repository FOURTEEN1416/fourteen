import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Wand2 } from 'lucide-react'
import AnimatedPage from '../components/shared/AnimatedPage'
import { cloneTemplate, isCloneAccepted, listTemplates } from '../api/templates'
import type { CharacterTemplate } from '../api/templates'
import { queryKeys } from '../hooks/useQueries'
import { useErrorStore } from '../store/errorStore'

// ── W17 模板面契约（api/routers/character_template_routes.py）──
//
// GET  /api/character-templates            → {templates: [{id, name, description, tags}], total}
// POST /api/character-templates/{id}/clone → 201 {id, name, status: "created"}
//
// 克隆 = **新 id + 归属当前账号的独立副本**（模板本身是无人格归属的策展卡，
// 改名/改设定不会回头影响模板），所以成功后必须让「我的角色」查询失效——
// 否则 30s staleTime 内跳回角色页看不到刚创建的卡。

function TemplateCard({
  template,
  onClone,
  cloning,
}: {
  template: CharacterTemplate
  onClone: (template: CharacterTemplate) => void
  cloning: boolean
}) {
  return (
    <div className="glass-card rounded-2xl p-4 flex flex-col h-full stagger-item">
      <div className="text-sm font-semibold text-gray-800 mb-1 truncate">{template.name}</div>
      <div className="text-xs text-gray-500 mb-3 leading-relaxed line-clamp-3 min-h-[3rem]">
        {template.description}
      </div>
      <div className="flex flex-wrap gap-1 mb-4 min-h-[1.25rem] content-start">
        {(template.tags ?? []).map((tag) => (
          <span key={tag} className="tag-blue px-2 py-0.5 rounded text-[10px]">
            {tag}
          </span>
        ))}
      </div>
      <button
        onClick={() => onClone(template)}
        disabled={cloning}
        className="mt-auto w-full py-1.5 rounded-lg text-xs border border-macaron-blue/40 text-macaron-blue-deep hover:bg-macaron-blue-light/30 transition-colors disabled:opacity-50"
      >
        {cloning ? '创建中…' : '使用此角色'}
      </button>
    </div>
  )
}

export default function TemplatesPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [templates, setTemplates] = useState<CharacterTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [cloningId, setCloningId] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await listTemplates()
      setTemplates(res?.templates ?? [])
    } catch {
      setTemplates([])
      setError('无法加载角色模板')
    } finally {
      setLoading(false)
    }
  }, [])

  /* eslint-disable react-hooks/set-state-in-effect -- 挂载时拉取模板清单 */
  useEffect(() => {
    void load()
  }, [load])
  /* eslint-enable react-hooks/set-state-in-effect */

  const handleClone = async (template: CharacterTemplate) => {
    setCloningId(template.id)
    try {
      const receipt = await cloneTemplate(template.id)
      // W11 纪律：只认业务回执，HTTP 2xx 而 status 非 created 一律不算成功
      if (!isCloneAccepted(receipt)) {
        useErrorStore.getState().addToast({
          type: 'warning',
          message: `创建未生效（后端回执状态 ${receipt?.status ?? '未知'}），请重试`,
        })
        return
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.characters.all })
      useErrorStore.getState().addToast({
        type: 'success',
        message: `已为你创建「${receipt.name || template.name}」，在角色配置里设为活跃即可开始`,
      })
      navigate('/roles')
    } catch (err: unknown) {
      useErrorStore.getState().addToast({
        type: 'error',
        message: err instanceof Error ? err.message : '创建角色失败，请重试',
      })
    } finally {
      setCloningId(null)
    }
  }

  return (
    <AnimatedPage>
      <div className="px-4 py-6 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-6xl">
          <div className="mb-6">
            <h1 className="text-xl font-bold text-gray-800">角色模板</h1>
            <p className="mt-0.5 text-sm text-gray-400">
              挑一个模板开始陪伴。克隆后它是你名下独立的一份，可自由改名与调整设定。
            </p>
          </div>

          {loading ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="glass-card rounded-2xl p-4 h-44 animate-pulse bg-white/30" />
              ))}
            </div>
          ) : error ? (
            <div className="glass-card rounded-2xl p-6 text-center space-y-3">
              <p className="text-sm text-red-600">{error}</p>
              <button
                onClick={load}
                className="rounded-lg border border-red-200 bg-white/80 px-4 py-1.5 text-xs text-red-600 hover:bg-white transition-colors"
              >
                重试
              </button>
            </div>
          ) : templates.length === 0 ? (
            <div className="glass-card rounded-2xl p-8 text-center text-gray-400">
              <Wand2 className="w-10 h-10 mx-auto mb-3 text-gray-400" />
              <p className="text-sm">暂无可用模板</p>
              <p className="mt-1 text-xs">模板库由管理员策展，稍后再来看看，或直接创建自己的角色。</p>
              <button
                onClick={() => navigate('/roles/create')}
                className="mt-4 btn-macaron rounded-xl px-5 py-2 text-xs font-medium"
              >
                创建角色
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {templates.map((template, i) => (
                <div key={template.id} className="stagger-item h-full" style={{ animationDelay: `${Math.min(i, 12) * 60}ms` }}>
                  <TemplateCard
                    template={template}
                    onClone={handleClone}
                    cloning={cloningId === template.id}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </AnimatedPage>
  )
}
