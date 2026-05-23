import { useEffect } from 'react'
import { useChatStore } from '../../store/chatStore'

export default function ProactiveToast() {
  const proactiveMessage = useChatStore((s) => s.proactiveMessage)
  const setProactiveMessage = useChatStore((s) => s.setProactiveMessage)

  useEffect(() => {
    if (proactiveMessage) {
      const timer = setTimeout(() => setProactiveMessage(null), 8000)
      return () => clearTimeout(timer)
    }
  }, [proactiveMessage, setProactiveMessage])

  if (!proactiveMessage) return null

  return (
    <div className="fixed top-4 left-1/2 -translate-x-1/2 z-50">
      <div className="bg-gray-200/90 border border-gray-300/50 text-gray-800 px-4 py-2.5 rounded-xl shadow-lg backdrop-blur-sm">
        <div className="flex items-center gap-2 text-sm">
          <span>{proactiveMessage}</span>
          <button
            onClick={() => setProactiveMessage(null)}
            className="text-gray-400 hover:text-gray-700 ml-1 shrink-0"
          >
            ✕
          </button>
        </div>
      </div>
    </div>
  )
}
