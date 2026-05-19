import { create } from 'zustand'

export interface Toast {
  id: string
  message: string
  type: 'error' | 'warning' | 'info' | 'success'
  duration?: number
}

interface ErrorState {
  toasts: Toast[]
  lastError: string | null
  addToast: (toast: Omit<Toast, 'id'>) => void
  removeToast: (id: string) => void
  clearError: () => void
}

let toastId = 0

export const useErrorStore = create<ErrorState>((set) => ({
  toasts: [],
  lastError: null,

  addToast: (toast) => {
    const id = `toast-${++toastId}`
    const duration = toast.duration ?? 4000

    set((state) => ({
      toasts: [...state.toasts, { ...toast, id }],
      lastError: toast.type === 'error' ? toast.message : state.lastError,
    }))

    if (duration > 0) {
      setTimeout(() => {
        set((state) => ({
          toasts: state.toasts.filter((t) => t.id !== id),
        }))
      }, duration)
    }
  },

  removeToast: (id) =>
    set((state) => ({
      toasts: state.toasts.filter((t) => t.id !== id),
    })),

  clearError: () => set({ lastError: null }),
}))
