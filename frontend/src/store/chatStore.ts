import { create } from 'zustand'
import type { ChatMessage, EmotionState } from '../types/api'

const MAX_MESSAGES = 500

interface ChatState {
  messages: ChatMessage[]
  streamingMessage: ChatMessage | null
  sessionId: string
  isConnected: boolean
  isStreaming: boolean
  emotion: EmotionState | null
  affinity: number | null
  addMessage: (msg: ChatMessage) => void
  appendStreamToken: (token: string) => void
  finalizeStreamMessage: (interrupted?: boolean) => void
  setSessionId: (id: string) => void
  setConnected: (v: boolean) => void
  setStreaming: (v: boolean) => void
  setEmotion: (e: EmotionState | null) => void
  setAffinity: (value: number) => void
  clearMessages: () => void
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  streamingMessage: null,
  sessionId: '',
  isConnected: false,
  isStreaming: false,
  emotion: null,
  affinity: null,

  addMessage: (msg) =>
    set((state) => {
      const newMessages = [...state.messages, msg]
      if (newMessages.length > MAX_MESSAGES) {
        return { messages: newMessages.slice(-MAX_MESSAGES) }
      }
      return { messages: newMessages }
    }),

  appendStreamToken: (token: string) =>
    set((state) => ({
      streamingMessage: state.streamingMessage
        ? { ...state.streamingMessage, content: state.streamingMessage.content + token }
        : { role: 'assistant' as const, content: token, timestamp: Date.now() },
    })),

  finalizeStreamMessage: (interrupted = false) => {
    const { streamingMessage, messages } = get()
    if (!streamingMessage) return
    const finalMsg = interrupted ? { ...streamingMessage, interrupted: true } : streamingMessage
    const newMessages = [...messages, finalMsg]
    set({
      messages: newMessages.length > MAX_MESSAGES ? newMessages.slice(-MAX_MESSAGES) : newMessages,
      streamingMessage: null,
    })
  },

  setSessionId: (id) => set({ sessionId: id }),
  setConnected: (v) => set({ isConnected: v }),
  setStreaming: (v) => set({ isStreaming: v }),
  setEmotion: (e) => set({ emotion: e }),
  setAffinity: (value) => set({ affinity: value }),
  clearMessages: () => set({ messages: [], streamingMessage: null }),
}))
