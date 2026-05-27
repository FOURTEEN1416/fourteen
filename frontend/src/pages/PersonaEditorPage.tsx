import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useUnifiedCharacters } from '../hooks/useQueries'
import { api } from '../api/client'
import type { PersonaCardData } from '../api/characters'
import { useErrorStore } from '../store/errorStore'
import Card from '../components/common/Card'
import Button from '../components/common/Button'
import Badge from '../components/common/Badge'
import Skeleton from '../components/common/Skeleton'
import EmptyState from '../components/common/EmptyState'
import { PenTool, Save, RefreshCw, Eye, ChevronDown } from 'lucide-react'
import type { UnifiedCharacter } from '../types/api'

function getErrorMessage(e: unknown): string {
  if (e instanceof Error) return e.message
  return String(e)
}

export default function PersonaEditorPage() {
  const [selectedOverride, setSelectedOverride] = useState('')
  const [fieldOverrides, setFieldOverrides] = useState<Partial<PersonaCardData>>({})
  const [isDirty, setIsDirty] = useState(false)
  const [preview, setPreview] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const toast = useErrorStore.getState().addToast

  const { data: charData, isLoading: loading, error: charsError } = useUnifiedCharacters()
  const characters = (charData?.characters ?? []) as UnifiedCharacter[]

  useEffect(() => {
    if (charsError) toast({ type: 'error', message: getErrorMessage(charsError) || '加载角色列表失败' })
  }, [charsError, toast])

  // 派生当前选中角色：优先用户选择，否则取第一个活跃角色
  const defaultCharId = characters.find((c: UnifiedCharacter) => c.is_active)?.id || characters[0]?.id || ''
  const selected = selectedOverride || defaultCharId

  const { data: personaData, error: personaError, refetch: refetchPersona, isLoading: personaLoading } = useQuery({
    queryKey: ['persona-card', selected],
    queryFn: () => api.getPersonaCard(selected),
    enabled: !!selected,
  })

  useEffect(() => {
    if (personaError) toast({ type: 'error', message: getErrorMessage(personaError) || '加载人设失败' })
  }, [personaError, toast])

  // 合并后端数据与用户编辑覆盖
  const persona: PersonaCardData | null = personaData ? { ...personaData, ...fieldOverrides } : null

  const updateField = (key: keyof PersonaCardData, value: string | string[]) => {
    if (!personaData) return
    setFieldOverrides(prev => ({ ...prev, [key]: value }))
    setIsDirty(true)
  }

  async function handleSave() {
    if (!selected || !persona) return
    try {
      setSaving(true)
      await api.updatePersonaCard(selected, persona)
      setIsDirty(false)
      toast({ type: 'success', message: '人设保存成功' })
    } catch (e: unknown) {
      toast({ type: 'error', message: getErrorMessage(e) || '保存失败' })
    } finally { setSaving(false) }
  }

  async function handlePreview() {
    if (!selected) return
    try {
      const data = await api.previewPersonaCard(selected)
      setPreview(data.preview)
    } catch (e: unknown) {
      toast({ type: 'error', message: getErrorMessage(e) || '预览失败' })
    }
  }

  const textFields: { key: keyof PersonaCardData; label: string; rows?: number }[] = [
    { key: 'name', label: '名称' },
    { key: 'description', label: '描述', rows: 2 },
    { key: 'personality', label: '性格', rows: 3 },
    { key: 'scenario', label: '场景', rows: 3 },
    { key: 'first_mes', label: '首条消息', rows: 3 },
    { key: 'mes_example', label: '消息示例', rows: 4 },
    { key: 'creator_notes', label: '创作者备注', rows: 2 },
  ]

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold text-gray-800 flex items-center gap-2">
          <PenTool className="w-4 h-4 text-gray-500" />
          人设编辑器
        </h1>
        <div className="flex items-center gap-2">
          {characters.length > 0 && (
            <div className="relative">
              <select
                value={selected}
                onChange={e => { setSelectedOverride(e.target.value); setFieldOverrides({}); setIsDirty(false) }}
                className="appearance-none bg-white/80 border border-gray-200 rounded-lg pl-3 pr-8 py-1.5 text-xs text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary-400/30"
              >
                {characters.map((c: UnifiedCharacter) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
              <ChevronDown className="w-3.5 h-3.5 text-gray-400 absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
          )}
          {isDirty && <Badge variant="warning">未保存</Badge>}
          <Button variant="ghost" size="sm" onClick={() => refetchPersona()}>
            <RefreshCw className="w-3.5 h-3.5" />
          </Button>
          <Button variant="secondary" size="sm" onClick={handlePreview}>
            <Eye className="w-3.5 h-3.5 mr-1" /> 预览
          </Button>
          <Button variant="primary" size="sm" onClick={handleSave} loading={saving} disabled={!isDirty}>
            <Save className="w-3.5 h-3.5 mr-1" /> 保存
          </Button>
        </div>
      </div>

      {loading || personaLoading ? (
        <Card><Skeleton lines={8} /></Card>
      ) : !persona ? (
        <EmptyState icon="📝" title="请选择角色" description="从上方下拉选择一个角色以编辑人设" />
      ) : (
        <Card padding="lg">
          <div className="space-y-4">
            {textFields.map(({ key, label, rows }) => (
              <div key={key}>
                <label className="block text-xs font-medium text-gray-500 mb-1.5">{label}</label>
                {rows ? (
                  <textarea
                    value={(persona[key] ?? '') as string}
                    onChange={e => updateField(key, e.target.value)}
                    rows={rows}
                    className="w-full bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-xs text-gray-700 resize-y focus:outline-none focus:ring-2 focus:ring-primary-400/30"
                  />
                ) : (
                  <input
                    type="text"
                    value={(persona[key] ?? '') as string}
                    onChange={e => updateField(key, e.target.value)}
                    className="w-full bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-xs text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary-400/30"
                  />
                )}
              </div>
            ))}
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1.5">标签</label>
              <input
                type="text"
                value={(persona.tags ?? []).join(', ')}
                onChange={e => updateField('tags', e.target.value.split(',').map(s => s.trim()).filter(Boolean))}
                className="w-full bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-xs text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary-400/30"
                placeholder="标签1, 标签2, ..."
              />
            </div>
          </div>
        </Card>
      )}

      {preview && (
        <Card>
          <h2 className="text-sm font-semibold text-gray-700 mb-3">人设预览</h2>
          <pre className="text-xs text-gray-600 whitespace-pre-wrap bg-gray-50 rounded-lg p-3 overflow-x-auto">{preview}</pre>
        </Card>
      )}
    </div>
  )
}
