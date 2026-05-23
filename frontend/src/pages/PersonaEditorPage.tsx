import { useState, useEffect } from 'react'
import { shisiClient } from '../api/shisiClient'
import { useErrorStore } from '../store/errorStore'
import Card from '../components/common/Card'
import Button from '../components/common/Button'
import Badge from '../components/common/Badge'
import Skeleton from '../components/common/Skeleton'
import EmptyState from '../components/common/EmptyState'
import { PenTool, Save, RefreshCw, Eye, ChevronDown } from 'lucide-react'
import type { CharacterState } from '../types/character'

interface PersonaData {
  name: string
  description: string
  personality: string
  scenario: string
  first_mes: string
  mes_example: string
  creator_notes: string
  tags: string[]
}

export default function PersonaEditorPage() {
  const [characters, setCharacters] = useState<CharacterState[]>([])
  const [selected, setSelected] = useState('')
  const [persona, setPersona] = useState<PersonaData | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const toast = useErrorStore.getState().addToast

  useEffect(() => { loadCharacters() }, [])

  async function loadCharacters() {
    try {
      setLoading(true)
      const data = await shisiClient.characters.list() as CharacterState[]
      setCharacters(data)
      if (data.length > 0) {
        const active = data.find(c => c.is_active) || data[0]
        setSelected(active.character_id)
        loadPersona(active.character_id)
      }
    } catch (e: any) {
      toast({ type: 'error', message: e?.message || '加载角色列表失败' })
    } finally { setLoading(false) }
  }

  async function loadPersona(cid: string) {
    try {
      const data = await shisiClient.persona.get(cid) as PersonaData
      setPersona(data)
      setDirty(false)
    } catch (e: any) {
      toast({ type: 'error', message: e?.message || '加载人设失败' })
    }
  }

  async function handleSave() {
    if (!selected || !persona) return
    try {
      setSaving(true)
      await shisiClient.persona.update(selected, persona as unknown as Record<string, unknown>)
      setDirty(false)
      toast({ type: 'success', message: '人设保存成功' })
    } catch (e: any) {
      toast({ type: 'error', message: e?.message || '保存失败' })
    } finally { setSaving(false) }
  }

  async function handlePreview() {
    if (!selected) return
    try {
      const data = await shisiClient.persona.preview(selected) as { preview: string }
      setPreview(data.preview)
    } catch (e: any) {
      toast({ type: 'error', message: e?.message || '预览失败' })
    }
  }

  function updateField(key: keyof PersonaData, value: string | string[]) {
    if (!persona) return
    setPersona({ ...persona, [key]: value })
    setDirty(true)
  }

  const textFields: { key: keyof PersonaData; label: string; rows?: number }[] = [
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
                onChange={e => { setSelected(e.target.value); loadPersona(e.target.value) }}
                className="appearance-none bg-white/80 border border-gray-200 rounded-lg pl-3 pr-8 py-1.5 text-xs text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary-400/30"
              >
                {characters.map(c => <option key={c.character_id} value={c.character_id}>{c.name}</option>)}
              </select>
              <ChevronDown className="w-3.5 h-3.5 text-gray-400 absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
          )}
          {dirty && <Badge variant="warning">未保存</Badge>}
          <Button variant="ghost" size="sm" onClick={() => loadPersona(selected)}>
            <RefreshCw className="w-3.5 h-3.5" />
          </Button>
          <Button variant="secondary" size="sm" onClick={handlePreview}>
            <Eye className="w-3.5 h-3.5 mr-1" /> 预览
          </Button>
          <Button variant="primary" size="sm" onClick={handleSave} loading={saving} disabled={!dirty}>
            <Save className="w-3.5 h-3.5 mr-1" /> 保存
          </Button>
        </div>
      </div>

      {loading ? (
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
