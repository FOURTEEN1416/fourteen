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
  proactiveMessage: string | null
  currentCharacterId: string | null
  currentCharacterName: string | null
  emotionStage: string | null
  affinity: number | null
  lastSticker: { sticker_id: string; category: string } | null
  addMessage: (msg: ChatMessage) => void
  appendStreamToken: (token: string) => void
  finalizeStreamMessage: () => void
  setSessionId: (id: string) => void
  setConnected: (v: boolean) => void
  setStreaming: (v: boolean) => void
  setEmotion: (e: EmotionState | null) => void
  setProactiveMessage: (msg: string | null) => void
  setCurrentCharacter: (id: string, name: string) => void
  setEmotionStage: (stage: string) => void
  setAffinity: (value: number) => void
  setLastSticker: (sticker: { sticker_id: string; category: string }) => void
  clearMessages: () => void
  loadMoreMessages: (before: number) => Promise<void>
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  streamingMessage: null,
  sessionId: '',
  isConnected: false,
  isStreaming: false,
  emotion: null,
  proactiveMessage: null,
  currentCharacterId: null,
  currentCharacterName: null,
  emotionStage: null,
  affinity: null,
  lastSticker: null,

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

  finalizeStreamMessage: () => {
    const { streamingMessage, messages } = get()
    if (!streamingMessage) return
    const newMessages = [...messages, streamingMessage]
    set({
      messages: newMessages.length > MAX_MESSAGES ? newMessages.slice(-MAX_MESSAGES) : newMessages,
      streamingMessage: null,
    })
  },

  setSessionId: (id) => set({ sessionId: id }),
  setConnected: (v) => set({ isConnected: v }),
  setStreaming: (v) => set({ isStreaming: v }),
  setEmotion: (e) => set({ emotion: e }),
  setProactiveMessage: (msg) => set({ proactiveMessage: msg }),
  setCurrentCharacter: (id, name) => set({ currentCharacterId: id, currentCharacterName: name }),
  setEmotionStage: (stage) => set({ emotionStage: stage }),
  setAffinity: (value) => set({ affinity: value }),
  setLastSticker: (sticker) => set({ lastSticker: sticker }),
  clearMessages: () => set({ messages: [], streamingMessage: null }),

  loadMoreMessages: async (before: number) => {
    try {
      const resp = await fetch(`/api/chat/history?before=${before}&limit=50`)
      if (!resp.ok) return
      const olderMessages: ChatMessage[] = await resp.json()
      if (olderMessages.length > 0) {
        set((state) => ({ messages: [...olderMessages, ...state.messages] }))
      }
    } catch {
      // silently ignore
    }
  },
}))
