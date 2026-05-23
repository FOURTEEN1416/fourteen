import { useEffect, useState, useCallback } from 'react'
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
  const [sessionError, setSessionError] = useState(false)
  const [sessionLoading, setSessionLoading] = useState(true)

  const createSession = useCallback(async () => {
    setSessionError(false)
    setSessionLoading(true)
    try {
      const { data } = await api.createSession()
      const sid = (data as { session_id: string }).session_id
      setSessionId(sid)
    } catch {
      setSessionError(true)
    } finally {
      setSessionLoading(false)
    }
  }, [setSessionId])

  useEffect(() => {
    if (!sessionId) {
      createSession()
    } else {
      setSessionLoading(false)
    }
  }, [sessionId, createSession])

  const handleSend = (message: string) => {
    sendMessage(message, true)
  }

  return (
    <div className="flex-1 flex flex-col h-full">
      <ProactiveToast />

      <div className="flex-1 flex">
        <div className="flex-1 flex flex-col min-w-0">
          <div className="border-b border-gray-200 px-4 py-3 bg-gray-50">
            <h1 className="text-sm font-semibold text-gray-800">十四</h1>
          </div>

          {sessionError ? (
            <div className="flex-1 flex items-center justify-center p-8">
              <div className="text-center">
                <p className="text-sm text-red-400 mb-3">会话连接失败，请检查后端服务是否正常运行</p>
                <button
                  onClick={createSession}
                  className="px-4 py-2 bg-primary-500 text-white rounded-md text-sm hover:bg-primary-600 transition-colors"
                >
                  重试连接
                </button>
              </div>
            </div>
          ) : sessionLoading ? (
            <div className="flex-1 flex items-center justify-center">
              <p className="text-sm text-gray-400">连接中...</p>
            </div>
          ) : (
            <>
              <MessageList />
              <ChatInput onSend={handleSend} />
            </>
          )}
        </div>

        <aside className="hidden xl:flex flex-col w-64 border-l border-gray-200 p-3 gap-3 overflow-y-auto">
          <EmotionPanel />
        </aside>
      </div>
    </div>
  )
}
