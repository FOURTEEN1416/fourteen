import { useEffect, useRef, useCallback } from 'react'
import { useChatStore } from '../../store/chatStore'
import MessageBubble from './MessageBubble'
import TypingIndicator from './TypingIndicator'
import EmptyState from '../common/EmptyState'

declare const require: any

let FixedSizeList: any = null
let AutoSizer: any = null
;(() => {
  try {
    const rw = require('react-window') as any
    FixedSizeList = rw.FixedSizeList
    AutoSizer = (require('react-virtualized-auto-sizer') as any).default
  } catch {
    // fallback: no virtualization
  }
})()

const ITEM_SIZE = 80
const OVERSCAN_COUNT = 5

export default function MessageList() {
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
        <MessageBubble {...displayMessages[index]} />
      </div>
    ),
    [displayMessages]
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
    return (
      <div className="flex-1 overflow-hidden px-4 py-4">
        <AutoSizer>
          {({ height, width }: { height: number; width: number }) => (
            <FixedSizeList
              height={height}
              itemCount={displayMessages.length}
              itemSize={ITEM_SIZE}
              width={width}
              overscanCount={OVERSCAN_COUNT}
            >
              {Row}
            </FixedSizeList>
          )}
        </AutoSizer>
        {isStreaming && !streamingMessage && <TypingIndicator />}
        <div ref={bottomRef} />
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
      {displayMessages.map((msg, i) => (
        <MessageBubble key={i} {...msg} />
      ))}
      {isStreaming && !streamingMessage && <TypingIndicator />}
      <div ref={bottomRef} />
    </div>
  )
}
