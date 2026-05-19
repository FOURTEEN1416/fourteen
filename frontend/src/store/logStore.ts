import { create } from 'zustand'
import type { LogEntry, LogLevel } from '../types/api'

interface LogState {
  entries: LogEntry[]
  filter: LogLevel | ''
  search: string
  sseConnected: boolean
  sseReconnecting: boolean
  maxEntries: number
  appendEntry: (entry: LogEntry) => void
  setEntries: (entries: LogEntry[]) => void
  setFilter: (filter: LogLevel | '') => void
  setSearch: (search: string) => void
  setSseStatus: (connected: boolean, reconnecting?: boolean) => void
  clearEntries: () => void
}

export const useLogStore = create<LogState>((set, get) => ({
  entries: [],
  filter: '',
  search: '',
  sseConnected: false,
  sseReconnecting: false,
  maxEntries: 500,

  appendEntry: (entry) => {
    const { entries, maxEntries } = get()
    const next = [entry, ...entries]
    set({ entries: next.length > maxEntries ? next.slice(0, maxEntries) : next })
  },

  setEntries: (entries) => set({ entries }),
  setFilter: (filter) => set({ filter }),
  setSearch: (search) => set({ search }),
  setSseStatus: (connected, reconnecting = false) => set({ sseConnected: connected, sseReconnecting: reconnecting }),
  clearEntries: () => set({ entries: [] }),
}))
