import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { AnimatedPage, Skeleton, EmptyState } from '../components/shared'
import { listMyBindings, updateBinding, type WechatBindingDTO } from '../api/wechat'
import { listPresets, type PresetItem } from '../api/characters'
import { ArrowLeft, Check, Smartphone } from 'lucide-react'

export default function BindingDetailPage() {
  const { wxid } = useParams<{ wxid: string }>()
  const navigate = useNavigate()

  const [binding, setBinding] = useState<WechatBindingDTO | null>(null)
  const [presets, setPresets] = useState<PresetItem[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState<string | null>(null) // 正在保存的角色 ID

  // 加载数据
  useEffect(() => {
    if (!wxid) return
    let cancelled = false
    Promise.all([listMyBindings(), listPresets()])
      .then(([bindRes, presetRes]) => {
        if (cancelled) return
        const found = bindRes.data.bindings.find((b) => b.wxid === wxid)
        if (!found) {
          setLoading(false)
          return
        }
        setBinding(found)
        setPresets(presetRes.presets || [])
      })
      .catch((err: unknown) => console.warn('加载绑定详情失败:', err))
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [wxid])

  // 选择角色
  const handleSelectCharacter = useCallback(async (characterCardId: string) => {
    if (!wxid || saving) return
    setSaving(characterCardId)
    try {
      const res = await updateBinding(wxid, { character_card_id: characterCardId })
      setBinding(res.data.binding)
    } catch {
      // toast 已处理
    } finally {
      setSaving(null)
    }
  }, [wxid, saving])

  if (loading) {
    return (
      <AnimatedPage>
        <div className="px-4 py-6 sm:px-6 lg:px-8">
          <div className="mx-auto max-w-4xl space-y-4">
            <Skeleton className="h-8 w-48" />
            <Skeleton className="h-5 w-32" />
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="glass-card rounded-xl p-4 space-y-2">
                  <Skeleton className="h-20 w-full rounded-lg" />
                  <Skeleton className="h-4 w-20" />
                </div>
              ))}
            </div>
          </div>
        </div>
      </AnimatedPage>
    )
  }

  if (!binding) {
    return (
      <AnimatedPage>
        <div className="px-4 py-6 sm:px-6 lg:px-8">
          <div className="mx-auto max-w-4xl">
            <EmptyState icon="📱" title="绑定不存在" description="未找到该微信绑定" />
          </div>
        </div>
      </AnimatedPage>
    )
  }

  const displayName = binding.nickname || binding.wxid

  return (
    <AnimatedPage>
      <div className="px-4 py-6 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-4xl">
          {/* Back */}
          <button
            onClick={() => navigate('/users')}
            className="mb-4 flex items-center gap-1.5 text-sm text-gray-400 hover:text-gray-600 transition-colors"
          >
            <ArrowLeft className="h-4 w-4" />
            返回我的微信
          </button>

          {/* Header */}
          <div className="mb-6 flex items-center gap-4">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary-50 text-primary-500">
              <Smartphone className="h-6 w-6" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-gray-800">{displayName}</h1>
              <p className="text-sm text-gray-400">
                <code className="rounded bg-gray-100 px-1.5 py-0.5 text-xs">{binding.wxid}</code>
              </p>
            </div>
          </div>

          {/* Current character */}
          <div className="glass-card mb-6 rounded-xl p-4">
            <p className="text-xs font-medium text-gray-400 uppercase tracking-wider mb-1">当前角色</p>
            <p className="text-sm font-semibold text-gray-700">
              {binding.character_card_id && binding.character_card_id !== 'default'
                ? binding.character_card_id
                : '未选择角色（默认）'}
            </p>
            <p className="mt-1 text-xs text-gray-400">
              微信聊天时将使用所选角色回复
            </p>
          </div>

          {/* Preset grid */}
          <div>
            <h2 className="mb-3 text-sm font-semibold text-gray-700">选择一个角色</h2>
            {presets.length === 0 ? (
              <EmptyState icon="🎭" title="暂无角色预设" description="角色预设尚未加载" />
            ) : (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                {presets.map((preset) => {
                  const isSelected = binding.character_card_id === preset.id
                  const isSaving = saving === preset.id
                  const description = preset.description
                    ? (preset.description.length > 60
                        ? preset.description.slice(0, 60) + '...'
                        : preset.description)
                    : ''

                  return (
                    <button
                      key={preset.id}
                      onClick={() => handleSelectCharacter(preset.id)}
                      disabled={!!saving}
                      className={`glass-card-hover group relative rounded-xl p-3 text-left transition-all active:scale-[0.97] ${
                        isSelected
                          ? 'ring-2 ring-primary-500 bg-primary-50/30'
                          : ''
                      } ${saving ? 'opacity-60 pointer-events-none' : ''}`}
                    >
                      {isSelected && (
                        <span className="absolute right-2 top-2 flex h-5 w-5 items-center justify-center rounded-full bg-primary-500 text-white">
                          <Check className="h-3 w-3" />
                        </span>
                      )}
                      {isSaving && (
                        <span className="absolute right-2 top-2 flex h-5 w-5 items-center justify-center">
                          <svg className="h-4 w-4 animate-spin text-primary-500" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                          </svg>
                        </span>
                      )}

                      {/* Avatar placeholder */}
                      <div className="mb-2 flex h-16 w-full items-center justify-center rounded-lg bg-gradient-to-br from-gray-50 to-gray-100 text-2xl">
                        {preset.name?.charAt(0) || '🎭'}
                      </div>

                      <p className="truncate text-sm font-semibold text-gray-800 group-hover:text-primary-600 transition-colors">
                        {preset.name || preset.id}
                      </p>
                      {description && (
                        <p className="mt-0.5 text-[10px] leading-tight text-gray-400 line-clamp-2">
                          {description}
                        </p>
                      )}
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </AnimatedPage>
  )
}
