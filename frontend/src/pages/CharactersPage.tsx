import { useState, useRef } from 'react'
import { useQueryClient, useMutation } from '@tanstack/react-query'
import { shisiClient } from '../api/shisiClient'
import { useCharacters, queryKeys } from '../hooks/useQueries'
import { useErrorStore } from '../store/errorStore'
import Card from '../components/common/Card'
import Button from '../components/common/Button'
import Badge from '../components/common/Badge'
import Skeleton from '../components/common/Skeleton'
import EmptyState from '../components/common/EmptyState'
import { Users, RefreshCw, Trash2, ArrowRightLeft, Upload, Download, Pencil, X, Save } from 'lucide-react'
import type { CharacterState } from '../types/character'

function getErrorMessage(e: unknown): string {
  if (e instanceof Error) return e.message
  return String(e)
}

export default function CharactersPage() {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [editTags, setEditTags] = useState('')
  const importRef = useRef<HTMLInputElement>(null)
  const toast = useErrorStore.getState().addToast
  const queryClient = useQueryClient()

  const { data: characters = [], isLoading, isError, error } = useCharacters()

  const invalidateCharacters = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.characters.all })

  const switchMutation = useMutation({
    mutationFn: (id: string) => shisiClient.characters.switch(id),
    onSuccess: () => { toast({ type: 'success', message: '角色切换成功' }); invalidateCharacters() },
    onError: (e: unknown) => toast({ type: 'error', message: getErrorMessage(e) || '切换失败' }),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => shisiClient.characters.delete(id),
    onSuccess: () => { toast({ type: 'success', message: '角色已删除' }); invalidateCharacters() },
    onError: (e: unknown) => toast({ type: 'error', message: getErrorMessage(e) || '删除失败' }),
  })

  const importMutation = useMutation({
    mutationFn: (files: FileList) => {
      const promises = Array.from(files).map(f => shisiClient.characters.import_(f))
      return Promise.all(promises)
    },
    onSuccess: () => { toast({ type: 'success', message: '角色导入成功' }); invalidateCharacters() },
    onError: (e: unknown) => toast({ type: 'error', message: getErrorMessage(e) || '导入失败' }),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, name, tags }: { id: string; name: string; tags: string[] }) =>
      shisiClient.characters.update(id, { name, tags }),
    onSuccess: () => { toast({ type: 'success', message: '角色信息已更新' }); invalidateCharacters() },
    onError: (e: unknown) => toast({ type: 'error', message: getErrorMessage(e) || '更新失败' }),
  })

  async function handleExport(id: string) {
    try {
      const result = await shisiClient.characters.export_(id)
      const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' })
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

  function startEdit(c: CharacterState) {
    setEditingId(c.character_id)
    setEditName(c.name)
    setEditTags(c.tags.join(', '))
  }

  function saveEdit() {
    if (!editingId) return
    const tags = editTags.split(',').map(s => s.trim()).filter(Boolean)
    updateMutation.mutate({ id: editingId, name: editName, tags })
    setEditingId(null)
  }

  if (isLoading) return (
    <div className="flex-1 overflow-y-auto p-6 space-y-4">
      <h1 className="text-base font-semibold text-gray-800">角色管理</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {[1, 2, 3].map(i => <Card key={i}><Skeleton lines={3} /></Card>)}
      </div>
    </div>
  )

  if (isError) {
    toast({ type: 'error', message: getErrorMessage(error) || '加载角色失败' })
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold text-gray-800 flex items-center gap-2">
          <Users className="w-4 h-4 text-gray-500" />
          角色管理
        </h1>
        <div className="flex items-center gap-2">
          <input ref={importRef} type="file" accept=".json" multiple className="hidden" onChange={e => e.target.files && importMutation.mutate(e.target.files)} />
          <Button variant="secondary" size="sm" onClick={() => importRef.current?.click()}>
            <Upload className="w-3.5 h-3.5 mr-1" /> 导入
          </Button>
          <Button variant="ghost" size="sm" onClick={() => invalidateCharacters()}>
            <RefreshCw className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>

      {characters.length === 0 ? (
        <EmptyState icon="👤" title="暂无角色" description="点击上方「导入」按钮导入角色人设JSON文件" />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {(characters as CharacterState[]).map(c => (
            <Card key={c.character_id} className={c.is_active ? 'border-primary-300 bg-primary-50/30' : ''}>
              {editingId === c.character_id ? (
                <>
                  <div className="space-y-3 mb-3">
                    <div>
                      <label className="block text-[10px] text-gray-400 uppercase tracking-wider mb-1">名称</label>
                      <input
                        type="text" value={editName} onChange={e => setEditName(e.target.value)}
                        className="w-full bg-gray-50 border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary-400/30"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] text-gray-400 uppercase tracking-wider mb-1">标签</label>
                      <input
                        type="text" value={editTags} onChange={e => setEditTags(e.target.value)}
                        placeholder="标签1, 标签2"
                        className="w-full bg-gray-50 border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary-400/30"
                      />
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Button variant="primary" size="sm" onClick={saveEdit}>
                      <Save className="w-3 h-3 mr-1" /> 保存
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setEditingId(null)}>
                      <X className="w-3 h-3" />
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <div className="flex items-center justify-between mb-3">
                    <h2 className="text-sm font-semibold text-gray-700">{c.name}</h2>
                    {c.is_active && <Badge variant="info">活跃</Badge>}
                  </div>
                  <div className="space-y-1.5 mb-3">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-gray-400">格式</span>
                      <span className="text-gray-600">{c.format}</span>
                    </div>
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-gray-400">标签</span>
                      <span className="text-gray-600">{c.tags.join(', ') || '无'}</span>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {!c.is_active && (
                      <Button variant="secondary" size="sm" onClick={() => switchMutation.mutate(c.character_id)}>
                        <ArrowRightLeft className="w-3 h-3 mr-1" /> 切换
                      </Button>
                    )}
                    <Button variant="ghost" size="sm" onClick={() => startEdit(c)}>
                      <Pencil className="w-3 h-3 mr-1" /> 编辑
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => handleExport(c.character_id)}>
                      <Download className="w-3 h-3 mr-1" /> 导出
                    </Button>
                    <Button variant="danger" size="sm" onClick={() => {
                      if (confirm('确定删除此角色？')) deleteMutation.mutate(c.character_id)
                    }}>
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
