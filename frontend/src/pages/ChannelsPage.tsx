import { useState, useEffect } from 'react'
import { api } from '../api/client'

interface Channel {
  id: string; name: string
  status: 'connected' | 'disconnected' | 'connecting' | 'error'
  desc: string; meta?: string
}

export default function ChannelsPage() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [loading, setLoading] = useState<string | null>(null)

  useEffect(() => {
    api.channels().then(({ data }) => setChannels((data as { channels: Channel[] }).channels)).catch(() => {})
    const interval = setInterval(() => {
      api.channels().then(({ data }) => setChannels((data as { channels: Channel[] }).channels)).catch(() => {})
    }, 10000)
    return () => clearInterval(interval)
  }, [])

  const toggle = (id: string) => {
    const ch = channels.find(c => c.id === id)
    if (!ch) return
    if (ch.status === 'connected') {
      setChannels(prev => prev.map(c => c.id === id ? { ...c, status: 'disconnected' } : c))
    } else {
      setLoading(id)
      setChannels(prev => prev.map(c => c.id === id ? { ...c, status: 'connecting' } : c))
      // Simulate connection attempt — real backend not available yet
      setTimeout(() => {
        setChannels(prev => prev.map(c => {
          if (c.id !== id) return c
          if (id === 'wechat') return { ...c, status: 'disconnected', meta: '需要后端扫码登录' }
          return { ...c, status: 'connected', meta: '在线' }
        }))
        setLoading(null)
      }, 1500)
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h1 className="text-base font-semibold text-slate-200 mb-6">通道连接</h1>

      {channels.length === 0 ? (
        <div className="text-sm text-slate-500">加载中...</div>
      ) : (
        <div className="space-y-2">
          {channels.map(ch => {
            const on = ch.status === 'connected'
            return (
              <div key={ch.id}
                className="flex items-center gap-4 bg-slate-900/40 border border-slate-800/60 rounded-xl px-4 py-3">
                <div className={`w-2 h-2 rounded-full ${on ? 'bg-green-400' : 'bg-slate-600'}`} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-slate-200">{ch.name}</div>
                  <div className="text-xs text-slate-500">{ch.desc}</div>
                </div>
                {ch.meta && <div className="text-xs text-slate-500">{ch.meta}</div>}
                <button
                  onClick={() => toggle(ch.id)}
                  disabled={loading === ch.id || ch.status === 'connecting'}
                  className={`px-3 py-1.5 rounded-lg text-xs border transition-colors ${
                    on
                      ? 'text-red-400 border-red-700/30'
                      : 'text-primary-300 border-primary-700/30'
                  } disabled:opacity-50`}
                >
                  {loading === ch.id ? '连接中...' : on ? '断开' : '连接'}
                </button>
              </div>
            )
          })}
        </div>
      )}

      <div className="mt-6 text-xs text-slate-600 bg-slate-900/20 border border-slate-800/30 rounded-lg px-4 py-3">
        小暖支持多通道同时运行。微信通道需启动后端后扫码登录。
      </div>
    </div>
  )
}
