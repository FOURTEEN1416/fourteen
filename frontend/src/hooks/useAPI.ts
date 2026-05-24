// DEPRECATED: This file is kept for backward compatibility.
// Please use hooks from useQueries.ts instead for all new code.
//
// Migration guide:
// - useEmotionState() -> import { useEmotionState } from './useQueries'
// - useEmotionTrend(days) -> import { useEmotionTrend } from './useQueries'
// - usePersonaProfile() -> import { usePersonaProfile, usePersonaEvolutionLog } from './useQueries'
// - useMemoryFacts() -> import { useMemoryFacts } from './useQueries'
// - useHealth() -> import { useHealth } from './useQueries'
//
// These hooks will be removed in a future version.

import { useEmotionState, useEmotionTrend, usePersonaProfile, usePersonaEvolutionLog, useMemoryFacts, useHealth } from './useQueries'

// Re-export for backward compatibility
export { useEmotionState, useEmotionTrend, usePersonaProfile, usePersonaEvolutionLog, useMemoryFacts, useHealth }
