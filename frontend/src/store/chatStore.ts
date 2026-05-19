import { create } from 'zustand'
import type { ChatMessage, EmotionState } from '../types/api'

interface ChatState {
  messages: ChatMessage[]
  sessionId: string
  isConnected: boolean
  isStreaming: boolean
  emotion: EmotionState | null
  proactiveMessage: string | null
  addMessage: (msg: ChatMessage) => void
  setSessionId: (id: string) => void
  setConnected: (v: boolean) => void
  setStreaming: (v: boolean) => void
  setEmotion: (e: EmotionState | null) => void
  setProactiveMessage: (msg: string | null) => void
  clearMessages: () => void
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  sessionId: '',
  isConnected: false,
  isStreaming: false,
  emotion: null,
  proactiveMessage: null,

  addMessage: (msg) =>
    set((state) => ({ messages: [...state.messages, msg] })),

  setSessionId: (id) => set({ sessionId: id }),
  setConnected: (v) => set({ isConnected: v }),
  setStreaming: (v) => set({ isStreaming: v }),
  setEmotion: (e) => set({ emotion: e }),
  setProactiveMessage: (msg) => set({ proactiveMessage: msg }),
  clearMessages: () => set({ messages: [] }),
}))
