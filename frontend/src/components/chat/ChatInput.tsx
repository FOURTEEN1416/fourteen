import { useState, useRef, KeyboardEvent } from 'react'
import { Send, Paperclip, X } from 'lucide-react'
import { useChatStore } from '../../store/chatStore'
import { api } from '../../api/client'

interface Props {
  onSend: (message: string, messageType?: string, fileUrl?: string) => void
}

export default function ChatInput({ onSend }: Props) {
  const [text, setText] = useState('')
  const [uploading, setUploading] = useState(false)
  const [attachedFile, setAttachedFile] = useState<{ name: string; url: string; type: string } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const isStreaming = useChatStore((s) => s.isStreaming)

  const handleSend = () => {
    const msg = text.trim()
    if ((!msg && !attachedFile) || isStreaming) return
    if (attachedFile) {
      onSend(msg || `[发送了${attachedFile.type === 'image' ? '一张图片' : '一个文件'}]`, attachedFile.type, attachedFile.url)
      setAttachedFile(null)
    } else {
      onSend(msg)
    }
    setText('')
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      const r = await api.uploadFile(file)
      setAttachedFile({ name: r.data.filename, url: r.data.url, type: r.data.message_type })
    } catch { /* ignore */ }
    setUploading(false)
    if (fileRef.current) fileRef.current.value = ''
  }

  const removeAttachment = () => setAttachedFile(null)

  return (
    <div className="border-t border-gray-200 px-4 py-3 bg-gray-50">
      {attachedFile && (
        <div className="flex items-center gap-2 mb-2 px-1">
          <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full flex items-center gap-1">
            📎 {attachedFile.name}
            <button onClick={removeAttachment} className="hover:text-red-500"><X className="w-3 h-3" /></button>
          </span>
        </div>
      )}
      <div className="flex items-center gap-2">
        <input
          type="file"
          ref={fileRef}
          onChange={handleFileChange}
          accept="image/*"
          className="hidden"
        />
        <button
          onClick={() => fileRef.current?.click()}
          disabled={isStreaming || uploading}
          className="text-gray-400 hover:text-blue-500 disabled:opacity-30 transition-colors shrink-0"
          title="发送图片"
        >
          {uploading ? (
            <div className="w-4 h-4 border-2 border-gray-300 border-t-blue-500 rounded-full animate-spin" />
          ) : (
            <Paperclip className="w-4 h-4" />
          )}
        </button>
        <input
          ref={inputRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="说点什么..."
          className="flex-1 bg-gray-200/50 text-gray-800 placeholder-slate-500 rounded-xl px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-primary-400/30 border border-gray-300/50"
          disabled={isStreaming}
        />
        <button
          onClick={handleSend}
          disabled={(!text.trim() && !attachedFile) || isStreaming}
          className="bg-primary-600 hover:bg-primary-500 disabled:bg-gray-200 disabled:text-gray-300 text-white rounded-xl px-3 py-2.5 transition-colors shrink-0"
        >
          <Send className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}
