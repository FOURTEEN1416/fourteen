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
      <div className="bg-slate-800/90 border border-slate-700/50 text-slate-200 px-4 py-2.5 rounded-xl shadow-lg backdrop-blur-sm">
        <div className="flex items-center gap-2 text-sm">
          <span>{proactiveMessage}</span>
          <button
            onClick={() => setProactiveMessage(null)}
            className="text-slate-500 hover:text-slate-300 ml-1 shrink-0"
          >
            ✕
          </button>
        </div>
      </div>
    </div>
  )
}
