import { useState, useRef, KeyboardEvent } from 'react'
import { Send } from 'lucide-react'
import { useChatStore } from '../../store/chatStore'

interface Props {
  onSend: (message: string) => void
}

export default function ChatInput({ onSend }: Props) {
  const [text, setText] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const isStreaming = useChatStore((s) => s.isStreaming)

  const handleSend = () => {
    const msg = text.trim()
    if (!msg || isStreaming) return
    onSend(msg)
    setText('')
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="border-t border-slate-800 px-4 py-3 bg-slate-900/50">
      <div className="flex items-center gap-2">
        <input
          ref={inputRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="说点什么..."
          className="flex-1 bg-slate-800/50 text-slate-200 placeholder-slate-500 rounded-xl px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-primary-400/30 border border-slate-700/50"
          disabled={isStreaming}
        />

        <button
          onClick={handleSend}
          disabled={!text.trim() || isStreaming}
          className="bg-primary-600 hover:bg-primary-500 disabled:bg-slate-800 disabled:text-slate-600 text-white rounded-xl px-3 py-2.5 transition-colors shrink-0"
        >
          <Send className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}
