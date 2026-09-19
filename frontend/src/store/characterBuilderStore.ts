import { create } from 'zustand'
import { DEFAULT_PERSONALITY, DEFAULT_SPEAKING_STYLE } from '../constants/persona'

// ═══ Character Builder shared state ═══
// Used by CreateRole page to push live persona updates to sidebar

export interface PersonaState {
  name: string
  description: string
  anchors: string[]
  personality: Record<string, number>
  speakingStyle: Record<string, number>
}

interface CharacterBuilderState {
  persona: PersonaState | null
  importFile: File | null
  hasContent: boolean
  setPersona: (p: Partial<PersonaState>) => void
  setImportFile: (file: File | null) => void
  resetPersona: () => void
}

const EMPTY: PersonaState = {
  name: '',
  description: '',
  anchors: [],
  personality: { ...DEFAULT_PERSONALITY },
  speakingStyle: { ...DEFAULT_SPEAKING_STYLE },
}

export const useCharacterBuilderStore = create<CharacterBuilderState>((set) => ({
  persona: null,
  importFile: null,
  hasContent: false,
  setPersona: (update) =>
    set((state) => {
      const prev = state.persona || EMPTY
      const next = {
        ...prev,
        ...update,
        personality: { ...prev.personality, ...(update.personality || {}) },
        speakingStyle: { ...prev.speakingStyle, ...(update.speakingStyle || {}) },
      }
      return {
        persona: next,
        hasContent: next.name !== '' || next.anchors.length > 0 || next.description !== '',
      }
    }),
  setImportFile: (file) => set({ importFile: file }),
  resetPersona: () => set({ persona: null, importFile: null, hasContent: false }),
}))
