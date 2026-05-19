import { useEffect } from 'react'
import { useWebSocket } from '../hooks/useWebSocket'
import { useChatStore } from '../store/chatStore'
import { api } from '../api/client'
import MessageList from '../components/chat/MessageList'
import ChatInput from '../components/chat/ChatInput'
import EmotionPanel from '../components/emotion/EmotionPanel'
import ProactiveToast from '../components/chat/ProactiveToast'

export default function ChatPage() {
  const { sendMessage } = useWebSocket()
  const sessionId = useChatStore((s) => s.sessionId)
  const setSessionId = useChatStore((s) => s.setSessionId)

  useEffect(() => {
    if (!sessionId) {
      api.createSession().then(({ data }) => {
        const sid = (data as { session_id: string }).session_id
        setSessionId(sid)
      }).catch(() => {})
    }
  }, [sessionId, setSessionId])

  const handleSend = (message: string) => {
    sendMessage(message, true)
  }

  return (
    <div className="flex-1 flex flex-col h-full">
      <ProactiveToast />

      <div className="flex-1 flex">
        <div className="flex-1 flex flex-col min-w-0">
          <div className="border-b border-slate-800 px-4 py-3 bg-slate-900/30">
            <h1 className="text-sm font-semibold text-slate-200">小暖</h1>
          </div>
          <MessageList />
          <ChatInput onSend={handleSend} />
        </div>

        <aside className="hidden xl:flex flex-col w-64 border-l border-slate-800 p-3 gap-3 overflow-y-auto">
          <EmotionPanel />
        </aside>
      </div>
    </div>
  )
}
