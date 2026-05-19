import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import type { EmotionState, EmotionTrend, PersonaProfile, EvolutionLog, MemoryFact, HealthStatus } from '../types/api'

export function useEmotionState() {
  const [state, setState] = useState<EmotionState | null>(null)
  const [loading, setLoading] = useState(true)
  const fetch = useCallback(async () => {
    try {
      const { data } = await api.emotionState()
      setState(data as EmotionState)
    } catch { /* ignore */ }
    setLoading(false)
  }, [])
  useEffect(() => { fetch(); const t = setInterval(fetch, 10000); return () => clearInterval(t) }, [fetch])
  return { state, loading, refetch: fetch }
}

export function useEmotionTrend(days = 7) {
  const [trend, setTrend] = useState<EmotionTrend | null>(null)
  useEffect(() => {
    api.emotionTrend(days).then(({ data }) => setTrend(data as EmotionTrend)).catch(() => {})
  }, [days])
  return trend
}

export function usePersonaProfile() {
  const [profile, setProfile] = useState<PersonaProfile | null>(null)
  const [log, setLog] = useState<EvolutionLog['log']>([])
  useEffect(() => {
    Promise.all([
      api.personaProfile().then(({ data }) => setProfile(data as PersonaProfile)),
      api.personaEvolutionLog().then(({ data }) => setLog((data as EvolutionLog).log)),
    ]).catch(() => {})
  }, [])
  return { profile, log }
}

export function useMemoryFacts() {
  const [facts, setFacts] = useState<MemoryFact[]>([])
  const [loading, setLoading] = useState(true)
  const fetch = useCallback(async (category = '') => {
    setLoading(true)
    try {
      const { data } = await api.memoryFacts(category)
      setFacts((data as { facts: MemoryFact[] }).facts)
    } catch { /* ignore */ }
    setLoading(false)
  }, [])
  useEffect(() => { fetch() }, [fetch])
  return { facts, loading, refetch: fetch }
}

export function useHealth() {
  const [health, setHealth] = useState<HealthStatus | null>(null)
  useEffect(() => {
    api.health().then(({ data }) => setHealth(data as HealthStatus)).catch(() => {})
    const t = setInterval(() => {
      api.health().then(({ data }) => setHealth(data as HealthStatus)).catch(() => {})
    }, 15000)
    return () => clearInterval(t)
  }, [])
  return health
}
