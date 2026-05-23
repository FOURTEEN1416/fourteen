import { memo } from 'react'

interface Props {
  role: 'user' | 'assistant'
  content: string
  emotion?: string
  timestamp?: number
}

function MessageBubbleInner({ role, content, emotion }: Props) {
  const isUser = role === 'user'

  return (
    <div className={`flex gap-2 items-end ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      {!isUser && (
        <div className="w-7 h-7 rounded-full bg-gray-200 flex items-center justify-center shrink-0 text-xs">
          🤖
        </div>
      )}
      <div className={`max-w-[75%] ${isUser ? 'items-end' : 'items-start'}`}>
        <div
          className={`px-4 py-2.5 text-sm leading-relaxed ${
            isUser
              ? 'bg-primary-600/30 text-primary-100 rounded-2xl rounded-br-md'
              : 'bg-gray-200/70 text-gray-800 rounded-2xl rounded-bl-md border border-gray-300/50'
          }`}
        >
          {content}
        </div>
        {!isUser && emotion && (
          <div className="text-[10px] text-gray-400 mt-0.5 px-1">{emotion}</div>
        )}
      </div>
    </div>
  )
}

const MessageBubble = memo(MessageBubbleInner, (prev, next) => {
  return prev.content === next.content &&
         prev.role === next.role &&
         prev.emotion === next.emotion
})

export default MessageBubble
