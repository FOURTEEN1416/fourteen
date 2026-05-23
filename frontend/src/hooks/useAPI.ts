import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import type { EmotionState, EmotionTrend, PersonaProfile, EvolutionLog, MemoryFact, HealthStatus } from '../types/api'

const EMOTION_POLL_INTERVAL = 10000
const HEALTH_POLL_INTERVAL = 15000

export function useEmotionState() {
  const [state, setState] = useState<EmotionState | null>(null)
  const [loading, setLoading] = useState(true)
  const fetch = useCallback(async (signal?: AbortSignal) => {
    try {
      const { data } = await api.emotionState()
      if (!signal?.aborted) setState(data as EmotionState)
    } catch { /* ignore */ }
    if (!signal?.aborted) setLoading(false)
  }, [])
  useEffect(() => {
    const ac = new AbortController()
    fetch(ac.signal)
    const t = setInterval(() => fetch(ac.signal), EMOTION_POLL_INTERVAL)
    return () => { ac.abort(); clearInterval(t) }
  }, [fetch])
  return { state, loading, refetch: fetch }
}

export function useEmotionTrend(days = 7) {
  const [trend, setTrend] = useState<EmotionTrend | null>(null)
  useEffect(() => {
    const ac = new AbortController()
    api.emotionTrend(days).then(({ data }) => { if (!ac.signal.aborted) setTrend(data as EmotionTrend) }).catch(() => {})
    return () => ac.abort()
  }, [days])
  return trend
}

export function usePersonaProfile() {
  const [profile, setProfile] = useState<PersonaProfile | null>(null)
  const [log, setLog] = useState<EvolutionLog['log']>([])
  useEffect(() => {
    const ac = new AbortController()
    Promise.all([
      api.personaProfile().then(({ data }) => { if (!ac.signal.aborted) setProfile(data as PersonaProfile) }),
      api.personaEvolutionLog().then(({ data }) => { if (!ac.signal.aborted) setLog((data as EvolutionLog).log) }),
    ]).catch(() => {})
    return () => ac.abort()
  }, [])
  return { profile, log }
}

export function useMemoryFacts() {
  const [facts, setFacts] = useState<MemoryFact[]>([])
  const [loading, setLoading] = useState(true)
  const fetch = useCallback(async (category = '', signal?: AbortSignal) => {
    setLoading(true)
    try {
      const { data } = await api.memoryFacts(category)
      if (!signal?.aborted) setFacts((data as { facts: MemoryFact[] }).facts)
    } catch { /* ignore */ }
    if (!signal?.aborted) setLoading(false)
  }, [])
  useEffect(() => {
    const ac = new AbortController()
    fetch('', ac.signal)
    return () => ac.abort()
  }, [fetch])
  return { facts, loading, refetch: fetch }
}

export function useHealth() {
  const [health, setHealth] = useState<HealthStatus | null>(null)
  useEffect(() => {
    const ac = new AbortController()
    const doFetch = () => {
      api.health().then(({ data }) => { if (!ac.signal.aborted) setHealth(data as HealthStatus) }).catch(() => {})
    }
    doFetch()
    const t = setInterval(doFetch, HEALTH_POLL_INTERVAL)
    return () => { ac.abort(); clearInterval(t) }
  }, [])
  return health
}
