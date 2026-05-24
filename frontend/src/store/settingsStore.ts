import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface SettingsState {
  useStreaming: boolean
  setUseStreaming: (v: boolean) => void
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      useStreaming: true,
      setUseStreaming: (v) => set({ useStreaming: v }),
    }),
    { name: 'app-settings' }
  )
)
