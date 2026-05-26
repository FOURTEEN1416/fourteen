import { useState, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { characterApi } from '../api/characterApi'
import {
  useUnifiedCharacters,
  useCreateCharacter,
  useDeleteCharacter,
  useActivateCharacter,
  useUpdateCharacter,
  queryKeys,
} from '../hooks/useQueries'
import { useErrorStore } from '../store/errorStore'
import Card from '../components/common/Card'
import Button from '../components/common/Button'
import Badge from '../components/common/Badge'
import Skeleton from '../components/common/Skeleton'
import EmptyState from '../components/common/EmptyState'
import { Users, RefreshCw, Trash2, ArrowRightLeft, Upload, Download, Pencil, X, Save, Plus } from 'lucide-react'
import type { UnifiedCharacter } from '../types/api'

function getErrorMessage(e: unknown): string {
  if (e instanceof Error) return e.message
  return String(e)
}

function serializeRecord(record: Record<string, number>): string {
  return Object.entries(record).map(([k, v]) => `${k}: ${v}`).join(', ')
}

function parseRecord(input: string): Record<string, number> {
  const result: Record<string, number> = {}
  input.split(',').map(s => s.trim()).filter(Boolean).forEach(pair => {
    const sepIdx = pair.indexOf(':')
    if (sepIdx === -1) return
    const key = pair.slice(0, sepIdx).trim()
    const val = pair.slice(sepIdx + 1).trim()
    if (key) {
      const num = parseFloat(val)
      if (!isNaN(num)) result[key] = num
    }
  })
  return result
}

export default function CharactersPage() {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [editPersonality, setEditPersonality] = useState('')
  const [editSpeakingStyle, setEditSpeakingStyle] = useState('')
  const [editCoreAnchors, setEditCoreAnchors] = useState('')

  const [showCreate, setShowCreate] = useState(false)
  const [createName, setCreateName] = useState('')
  const [createDescription, setCreateDescription] = useState('')
  const [createPersonality, setCreatePersonality] = useState('')
  const [createSpeakingStyle, setCreateSpeakingStyle] = useState('')
  const [createCoreAnchors, setCreateCoreAnchors] = useState('')

  const importRef = useRef<HTMLInputElement>(null)
  const toast = useErrorStore.getState().addToast
  const queryClient = useQueryClient()

  const { data, isLoading, isError, error } = useUnifiedCharacters()
  const characters = data?.characters ?? []

  const activateMutation = useActivateCharacter()
  const deleteMutation = useDeleteCharacter()
  const createMutation = useCreateCharacter()
  const updateMutation = useUpdateCharacter()

  const invalidateCharacters = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.characters.all })

  // ── Handlers ──

  async function handleActivate(id: string) {
    try {
      await activateMutation.mutateAsync(id)
      toast({ type: 'success', message: '角色激活成功' })
    } catch (e: unknown) {
      toast({ type: 'error', message: getErrorMessage(e) || '激活失败' })
    }
  }

  async function handleDelete(id: string) {
    if (!confirm('确定删除此角色？')) return
    try {
      await deleteMutation.mutateAsync(id)
      toast({ type: 'success', message: '角色已删除' })
    } catch (e: unknown) {
      toast({ type: 'error', message: getErrorMessage(e) || '删除失败' })
    }
  }

  async function handleCreate() {
    if (!createName.trim()) {
      toast({ type: 'error', message: '角色名称不能为空' })
      return
    }
    try {
      await createMutation.mutateAsync({
        name: createName.trim(),
        description: createDescription.trim() || undefined,
        personality: parseRecord(createPersonality),
        speaking_style: parseRecord(createSpeakingStyle),
        core_anchors: createCoreAnchors.split(',').map(s => s.trim()).filter(Boolean),
      })
      toast({ type: 'success', message: '角色创建成功' })
      setShowCreate(false)
      setCreateName('')
      setCreateDescription('')
      setCreatePersonality('')
      setCreateSpeakingStyle('')
      setCreateCoreAnchors('')
    } catch (e: unknown) {
      toast({ type: 'error', message: getErrorMessage(e) || '创建失败' })
    }
  }

  async function handleSaveEdit() {
    if (!editingId) return
    try {
      await updateMutation.mutateAsync({
        id: editingId,
        data: {
          name: editName.trim() || undefined,
          personality: parseRecord(editPersonality),
          speaking_style: parseRecord(editSpeakingStyle),
          core_anchors: editCoreAnchors.split(',').map(s => s.trim()).filter(Boolean),
        },
      })
      toast({ type: 'success', message: '角色信息已更新' })
      setEditingId(null)
    } catch (e: unknown) {
      toast({ type: 'error', message: getErrorMessage(e) || '更新失败' })
    }
  }

  function startEdit(c: UnifiedCharacter) {
    setEditingId(c.id)
    setEditName(c.name)
    setEditPersonality(serializeRecord(c.personality))
    setEditSpeakingStyle(serializeRecord(c.speaking_style))
    setEditCoreAnchors(c.core_anchors.join(', '))
  }

  async function handleExport(id: string) {
    try {
      const blob = await characterApi.export(id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `character_${id}.json`
      a.click()
      URL.revokeObjectURL(url)
      toast({ type: 'success', message: '角色导出成功' })
    } catch (e: unknown) {
      toast({ type: 'error', message: getErrorMessage(e) || '导出失败' })
    }
  }

  async function handleImport(files: FileList) {
    try {
      const promises = Array.from(files).map(f => characterApi.import(f))
      await Promise.all(promises)
      toast({ type: 'success', message: '角色导入成功' })
      invalidateCharacters()
    } catch (e: unknown) {
      toast({ type: 'error', message: getErrorMessage(e) || '导入失败' })
    }
  }

  // ── Loading ──

  if (isLoading) {
    return (
      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        <h1 className="text-base font-semibold text-black">角色管理</h1>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3].map(i => <Card key={i}><Skeleton lines={3} /></Card>)}
        </div>
      </div>
    )
  }

  if (isError) {
    toast({ type: 'error', message: getErrorMessage(error) || '加载角色失败' })
  }

  // ── Create dialog ──

  function renderCreateDialog() {
    if (!showCreate) return null
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20" onClick={() => setShowCreate(false)}>
        <div className="bg-white rounded-xl shadow-xl border border-gray-200 p-6 w-full max-w-md mx-4 space-y-4" onClick={e => e.stopPropagation()}>
          <h2 className="text-sm font-semibold text-black">新建角色</h2>
          <div className="space-y-3">
            <div>
              <label className="block text-[10px] text-gray-500 uppercase tracking-wider mb-1">名称</label>
              <input
                type="text" value={createName} onChange={e => setCreateName(e.target.value)}
                className="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs text-black focus:outline-none focus:ring-2 focus:ring-gray-400/30"
              />
            </div>
            <div>
              <label className="block text-[10px] text-gray-500 uppercase tracking-wider mb-1">描述</label>
              <input
                type="text" value={createDescription} onChange={e => setCreateDescription(e.target.value)}
                className="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs text-black focus:outline-none focus:ring-2 focus:ring-gray-400/30"
              />
            </div>
            <div>
              <label className="block text-[10px] text-gray-500 uppercase tracking-wider mb-1">性格特征</label>
              <input
                type="text" value={createPersonality} onChange={e => setCreatePersonality(e.target.value)}
                placeholder="温柔: 0.8, 理性: 0.6"
                className="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs text-black focus:outline-none focus:ring-2 focus:ring-gray-400/30"
              />
            </div>
            <div>
              <label className="block text-[10px] text-gray-500 uppercase tracking-wider mb-1">说话风格</label>
              <input
                type="text" value={createSpeakingStyle} onChange={e => setCreateSpeakingStyle(e.target.value)}
                placeholder="正式: 0.7, 幽默: 0.5"
                className="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs text-black focus:outline-none focus:ring-2 focus:ring-gray-400/30"
              />
            </div>
            <div>
              <label className="block text-[10px] text-gray-500 uppercase tracking-wider mb-1">核心锚点</label>
              <input
                type="text" value={createCoreAnchors} onChange={e => setCreateCoreAnchors(e.target.value)}
                placeholder="锚点1, 锚点2"
                className="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs text-black focus:outline-none focus:ring-2 focus:ring-gray-400/30"
              />
            </div>
          </div>
          <div className="flex gap-2 pt-2">
            <Button variant="primary" size="sm" onClick={handleCreate} disabled={createMutation.isPending}>
              <Save className="w-3 h-3 mr-1" /> 创建
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setShowCreate(false)}>
              <X className="w-3 h-3 mr-1" /> 取消
            </Button>
          </div>
        </div>
      </div>
    )
  }

  // ── Main render ──

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      {renderCreateDialog()}

      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold text-black flex items-center gap-2">
          <Users className="w-4 h-4 text-gray-500" />
          角色管理
        </h1>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={() => {
            setCreateName('')
            setCreateDescription('')
            setCreatePersonality('')
            setCreateSpeakingStyle('')
            setCreateCoreAnchors('')
            setShowCreate(true)
          }}>
            <Plus className="w-3.5 h-3.5 mr-1" /> 新建
          </Button>
          <input
            ref={importRef}
            type="file" accept=".json" multiple className="hidden"
            onChange={e => e.target.files && handleImport(e.target.files)}
          />
          <Button variant="secondary" size="sm" onClick={() => importRef.current?.click()}>
            <Upload className="w-3.5 h-3.5 mr-1" /> 导入
          </Button>
          <Button variant="ghost" size="sm" onClick={invalidateCharacters}>
            <RefreshCw className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>

      {characters.length === 0 ? (
        <EmptyState icon="" title="暂无角色" description="点击上方「新建」按钮创建角色，或导入角色人设 JSON 文件" />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {characters.map(c => (
            <Card key={c.id}>
              {editingId === c.id ? (
                <>
                  <div className="space-y-3 mb-3">
                    <div>
                      <label className="block text-[10px] text-gray-500 uppercase tracking-wider mb-1">名称</label>
                      <input
                        type="text" value={editName} onChange={e => setEditName(e.target.value)}
                        className="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs text-black focus:outline-none focus:ring-2 focus:ring-gray-400/30"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] text-gray-500 uppercase tracking-wider mb-1">性格特征</label>
                      <input
                        type="text" value={editPersonality} onChange={e => setEditPersonality(e.target.value)}
                        placeholder="温柔: 0.8, 理性: 0.6"
                        className="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs text-black focus:outline-none focus:ring-2 focus:ring-gray-400/30"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] text-gray-500 uppercase tracking-wider mb-1">说话风格</label>
                      <input
                        type="text" value={editSpeakingStyle} onChange={e => setEditSpeakingStyle(e.target.value)}
                        placeholder="正式: 0.7, 幽默: 0.5"
                        className="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs text-black focus:outline-none focus:ring-2 focus:ring-gray-400/30"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] text-gray-500 uppercase tracking-wider mb-1">核心锚点</label>
                      <input
                        type="text" value={editCoreAnchors} onChange={e => setEditCoreAnchors(e.target.value)}
                        placeholder="锚点1, 锚点2"
                        className="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs text-black focus:outline-none focus:ring-2 focus:ring-gray-400/30"
                      />
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Button variant="primary" size="sm" onClick={handleSaveEdit} disabled={updateMutation.isPending}>
                      <Save className="w-3 h-3 mr-1" /> 保存
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setEditingId(null)}>
                      <X className="w-3 h-3" />
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <div className="flex items-center justify-between mb-2">
                    <h2 className="text-sm font-semibold text-black">{c.name}</h2>
                    {c.is_active && <Badge variant="info">活跃</Badge>}
                  </div>
                  {c.description && (
                    <p className="text-xs text-gray-500 mb-2 line-clamp-2">{c.description}</p>
                  )}
                  <div className="space-y-1 mb-3">
                    {c.personality && Object.keys(c.personality).length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {Object.entries(c.personality).map(([key, val]) => (
                          <Badge key={key} variant="default">{key}: {val}</Badge>
                        ))}
                      </div>
                    )}
                    {c.core_anchors && c.core_anchors.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {c.core_anchors.map((anchor, i) => (
                          <Badge key={i} variant="default">{anchor}</Badge>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {!c.is_active && (
                      <Button variant="secondary" size="sm" onClick={() => handleActivate(c.id)} disabled={activateMutation.isPending}>
                        <ArrowRightLeft className="w-3 h-3 mr-1" /> 激活
                      </Button>
                    )}
                    <Button variant="ghost" size="sm" onClick={() => startEdit(c)}>
                      <Pencil className="w-3 h-3 mr-1" /> 编辑
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => handleExport(c.id)}>
                      <Download className="w-3 h-3 mr-1" /> 导出
                    </Button>
                    <Button variant="danger" size="sm" onClick={() => handleDelete(c.id)} disabled={deleteMutation.isPending}>
                      <Trash2 className="w-3 h-3 mr-1" /> 删除
                    </Button>
                  </div>
                </>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
