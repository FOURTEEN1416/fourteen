import { useEffect, useRef, useCallback } from 'react'
import { useChatStore } from '../../store/chatStore'
import MessageBubble from './MessageBubble'
import TypingIndicator from './TypingIndicator'
import EmptyState from '../common/EmptyState'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
declare const require: (id: string) => unknown

interface FixedSizeListProps {
  height: number
  itemCount: number
  itemSize: number
  width: number
  overscanCount?: number
  children: React.ComponentType<{ index: number; style: React.CSSProperties }>
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let FixedSizeList: React.ComponentType<FixedSizeListProps> | undefined = undefined

interface AutoSizerProps {
  children: (props: { height: number; width: number }) => React.ReactNode
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let AutoSizer: React.ComponentType<AutoSizerProps> | undefined = undefined

;(() => {
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const rw = require('react-window') as { FixedSizeList: React.ComponentType<FixedSizeListProps> }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const autoSizer = require('react-virtualized-auto-sizer') as { default: React.ComponentType<AutoSizerProps> }
    FixedSizeList = rw.FixedSizeList
    AutoSizer = autoSizer.default
  } catch {
    // fallback: no virtualization
  }
})()

const ITEM_SIZE = 80
const OVERSCAN_COUNT = 5

interface MessageListProps {
  onRetryStream?: () => void
}

export default function MessageList({ onRetryStream }: MessageListProps) {
  const messages = useChatStore((s) => s.messages)
  const streamingMessage = useChatStore((s) => s.streamingMessage)
  const isStreaming = useChatStore((s) => s.isStreaming)
  const bottomRef = useRef<HTMLDivElement>(null)

  const displayMessages = streamingMessage ? [...messages, streamingMessage] : messages

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [displayMessages, isStreaming])

  const Row = useCallback(
    ({ index, style }: { index: number; style: React.CSSProperties }) => (
      <div style={style}>
        <MessageBubble {...displayMessages[index]} onRetryStream={onRetryStream} />
      </div>
    ),
    [displayMessages, onRetryStream]
  )

  if (displayMessages.length === 0) {
    return (
      <EmptyState
        icon="💬"
        title="开始聊天"
        description="和十四说点什么吧，她会记住你的喜好"
        className="flex-1"
      />
    )
  }

  if (FixedSizeList && AutoSizer) {
    const ListComponent = FixedSizeList
    const SizerComponent = AutoSizer
    return (
      <div className="flex-1 overflow-hidden px-4 py-4">
        <SizerComponent>
          {({ height, width }: { height: number; width: number }) => (
            <ListComponent
              height={height}
              itemCount={displayMessages.length}
              itemSize={ITEM_SIZE}
              width={width}
              overscanCount={OVERSCAN_COUNT}
            >
              {Row}
            </ListComponent>
          )}
        </SizerComponent>
        {isStreaming && !streamingMessage && <TypingIndicator />}
        <div ref={bottomRef} />
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
      {displayMessages.map((msg, i) => (
        <MessageBubble key={i} {...msg} onRetryStream={onRetryStream} />
      ))}
      {isStreaming && !streamingMessage && <TypingIndicator />}
      <div ref={bottomRef} />
    </div>
  )
}
