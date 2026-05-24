import { useState, useEffect, useRef, useCallback } from 'react'
import { shisiClient } from '../api/shisiClient'
import { useErrorStore } from '../store/errorStore'
import Card from '../components/common/Card'
import Button from '../components/common/Button'
import Badge from '../components/common/Badge'
import Skeleton from '../components/common/Skeleton'
import EmptyState from '../components/common/EmptyState'
import { Sticker, Upload, Trash2, RefreshCw, Search } from 'lucide-react'

interface StickerItem {
  id: string
  name: string
  emotion_tags: string[]
  url: string
  character_id?: string
}

function getErrorMessage(e: unknown): string {
  if (e instanceof Error) return e.message
  return String(e)
}

export default function StickersPage() {
  const [stickers, setStickers] = useState<StickerItem[]>([])
  const [loading, setLoading] = useState(true)
  const [emotion, setEmotion] = useState('')
  const [recommended, setRecommended] = useState<StickerItem[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)
  const zipInputRef = useRef<HTMLInputElement>(null)
  const toast = useErrorStore.getState().addToast

  const loadStickers = useCallback(async () => {
    try {
      setLoading(true)
      const data = await shisiClient.stickers.list() as StickerItem[]
      setStickers(Array.isArray(data) ? data : [])
    } catch (e: unknown) {
      toast({ type: 'error', message: getErrorMessage(e) || '加载表情包失败' })
    } finally { setLoading(false) }
  }, [toast])

  useEffect(() => { loadStickers() }, [loadStickers])

  async function handleDelete(id: string) {
    if (!confirm('确定删除此表情包？')) return
    try {
      await shisiClient.stickers.delete(id)
      toast({ type: 'success', message: '表情包已删除' })
      await loadStickers()
    } catch (e: unknown) {
      toast({ type: 'error', message: getErrorMessage(e) || '删除失败' })
    }
  }

  async function handleUpload(files: FileList | null) {
    if (!files || files.length === 0) return
    try {
      const formData = new FormData()
      Array.from(files).forEach(f => formData.append('files', f))
      await shisiClient.stickers.upload(formData)
      toast({ type: 'success', message: '表情包上传成功' })
      await loadStickers()
    } catch (e: unknown) {
      toast({ type: 'error', message: getErrorMessage(e) || '上传失败' })
    }
  }

  async function handleImportZip(files: FileList | null) {
    if (!files || files.length === 0) return
    try {
      const formData = new FormData()
      formData.append('zip', files[0])
      await shisiClient.stickers.importZip(formData)
      toast({ type: 'success', message: 'ZIP导入成功' })
      await loadStickers()
    } catch (e: unknown) {
      toast({ type: 'error', message: getErrorMessage(e) || 'ZIP导入失败' })
    }
  }

  async function handleRecommend() {
    if (!emotion.trim()) return
    try {
      const data = await shisiClient.stickers.recommend([emotion]) as StickerItem[]
      setRecommended(Array.isArray(data) ? data : [])
    } catch (e: unknown) {
      toast({ type: 'error', message: getErrorMessage(e) || '推荐失败' })
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold text-gray-800 flex items-center gap-2">
          <Sticker className="w-4 h-4 text-gray-500" />
          表情包管理
        </h1>
        <div className="flex items-center gap-2">
          <input ref={fileInputRef} type="file" accept="image/*" multiple className="hidden" onChange={e => handleUpload(e.target.files)} />
          <Button variant="secondary" size="sm" onClick={() => fileInputRef.current?.click()}>
            <Upload className="w-3.5 h-3.5 mr-1" /> 上传图片
          </Button>
          <input ref={zipInputRef} type="file" accept=".zip" className="hidden" onChange={e => handleImportZip(e.target.files)} />
          <Button variant="secondary" size="sm" onClick={() => zipInputRef.current?.click()}>
            <Upload className="w-3.5 h-3.5 mr-1" /> 导入ZIP
          </Button>
          <Button variant="ghost" size="sm" onClick={loadStickers}>
            <RefreshCw className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>

      <Card>
        <h2 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
          <Search className="w-4 h-4 text-gray-500" />
          情感推荐
        </h2>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={emotion}
            onChange={e => setEmotion(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleRecommend()}
            placeholder="输入情感关键词，如：开心、难过..."
            className="flex-1 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-xs text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary-400/30"
          />
          <Button variant="primary" size="sm" onClick={handleRecommend}>推荐</Button>
        </div>
        {recommended.length > 0 && (
          <div className="grid grid-cols-4 md:grid-cols-6 lg:grid-cols-8 gap-2 mt-3">
            {recommended.map(s => (
              <div key={s.id} className="aspect-square rounded-lg overflow-hidden bg-gray-50 border border-gray-200">
                <img src={s.url} alt={s.name} className="w-full h-full object-cover" />
              </div>
            ))}
          </div>
        )}
      </Card>

      {loading ? (
        <div className="grid grid-cols-4 md:grid-cols-6 lg:grid-cols-8 gap-2">
          {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="aspect-square rounded-lg" />)}
        </div>
      ) : stickers.length === 0 ? (
        <EmptyState icon="🎨" title="暂无表情包" description="上传图片或导入ZIP包添加表情包" />
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
          {stickers.map(s => (
            <Card key={s.id} padding="sm" hover>
              <div className="aspect-square rounded-lg overflow-hidden bg-gray-50 mb-2">
                <img src={s.url} alt={s.name} className="w-full h-full object-cover" />
              </div>
              <div className="flex items-center justify-between">
                <span className="text-[10px] text-gray-500 truncate max-w-[80%]">{s.name}</span>
                <button onClick={() => handleDelete(s.id)} className="text-gray-300 hover:text-red-400 transition-colors">
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
              {s.emotion_tags.length > 0 && (
                <div className="flex flex-wrap gap-0.5 mt-1">
                  {s.emotion_tags.slice(0, 3).map(t => <Badge key={t} variant="info" className="text-[9px]">{t}</Badge>)}
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
