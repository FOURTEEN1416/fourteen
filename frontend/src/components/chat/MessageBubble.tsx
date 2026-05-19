interface Props {
  role: 'user' | 'assistant'
  content: string
  emotion?: string
  timestamp?: number
}

export default function MessageBubble({ role, content, emotion }: Props) {
  const isUser = role === 'user'

  return (
    <div className={`flex gap-2 items-end ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      {!isUser && (
        <div className="w-7 h-7 rounded-full bg-slate-700 flex items-center justify-center shrink-0 text-xs">
          🤖
        </div>
      )}
      <div className={`max-w-[75%] ${isUser ? 'items-end' : 'items-start'}`}>
        <div
          className={`px-4 py-2.5 text-sm leading-relaxed ${
            isUser
              ? 'bg-primary-600/30 text-primary-100 rounded-2xl rounded-br-md'
              : 'bg-slate-800/70 text-slate-200 rounded-2xl rounded-bl-md border border-slate-700/50'
          }`}
        >
          {content}
        </div>
        {!isUser && emotion && (
          <div className="text-[10px] text-slate-500 mt-0.5 px-1">{emotion}</div>
        )}
      </div>
    </div>
  )
}
