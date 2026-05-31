import { useState, useEffect } from 'react'
import { Toggle } from '../components/shared'
import { config as fetchConfig, saveConfig } from '../api/system'

function SettingsGeneral() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  // Form state
  const [llmCache, setLlmCache] = useState(true)
  const [cacheDuration, setCacheDuration] = useState(30)
  const [llmProvider, setLlmProvider] = useState('')
  const [llmTemperature, setLlmTemperature] = useState(0.85)

  // Load config on mount
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    fetchConfig()
      .then(res => {
        if (cancelled) return
        const cfg = res.data ?? {}
        const llm = cfg.llm ?? {}
        setLlmProvider(llm.provider ?? '')
        setLlmTemperature(llm.temperature ?? 0.85)
        // llm cache: try cfg.llm_cache or cfg.cache
        const cache = cfg.llm_cache ?? cfg.cache ?? {}
        setLlmCache(cache.enabled !== false)
        setCacheDuration(cache.duration ?? cache.ttl ?? 30)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : '加载配置失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => { cancelled = true }
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    setSuccess(false)

    try {
      await saveConfig({
        llm: {
          provider: llmProvider,
          temperature: llmTemperature,
        },
        llm_cache: {
          enabled: llmCache,
          duration: cacheDuration,
        },
      })
      setSuccess(true)
      setTimeout(() => setSuccess(false), 3000)
    } catch {
      // 全局 interceptor 已自动显示 toast 通知
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[200px]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-500 border-t-transparent" />
        <span className="ml-2 text-sm text-gray-400">加载配置中...</span>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
          {error}
          <button onClick={() => setError(null)} className="float-right font-semibold">&times;</button>
        </div>
      )}
      {success && (
        <div className="bg-green-50 border border-green-200 text-green-700 rounded-lg px-4 py-3 text-sm">
          设置已保存
        </div>
      )}

      {/* LLM Provider */}
      <section>
        <h3 className="text-sm font-semibold text-gray-700 mb-3">LLM 配置</h3>
        <div className="glass-card rounded-xl p-4 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-gray-700">供应商标识</p>
              <p className="text-xs text-gray-400">LLM 提供商（如 auto, zhipu, xunfei, baidu）</p>
            </div>
            <input
              type="text"
              value={llmProvider}
              onChange={(e) => setLlmProvider(e.target.value)}
              className="w-40 px-3 py-1.5 text-xs glass-card rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50 text-center"
            />
          </div>
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-gray-700">温度</p>
              <p className="text-xs text-gray-400">生成随机性（0-1）</p>
            </div>
            <input
              type="number"
              value={llmTemperature}
              onChange={(e) => setLlmTemperature(parseFloat(e.target.value) || 0.85)}
              min={0}
              max={1}
              step={0.05}
              className="w-24 px-3 py-1.5 text-xs glass-card rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50 text-center"
            />
          </div>
        </div>
      </section>

      {/* LLM Cache */}
      <section>
        <h3 className="text-sm font-semibold text-gray-700 mb-3">LLM 缓存</h3>
        <div className="glass-card rounded-xl p-4 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-gray-700">启用缓存</p>
              <p className="text-xs text-gray-400">缓存 LLM 响应以减少重复请求</p>
            </div>
            <Toggle
              checked={llmCache}
              onChange={(v: boolean) => setLlmCache(v)}
            />
          </div>
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-gray-700">缓存时长</p>
              <p className="text-xs text-gray-400">缓存保留时间（分钟）</p>
            </div>
            <input
              type="number"
              value={cacheDuration}
              onChange={(e) => setCacheDuration(parseInt(e.target.value) || 0)}
              min={1}
              max={1440}
              className="w-24 px-3 py-1.5 text-xs glass-card rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50 transition-all text-center"
            />
          </div>
        </div>
      </section>

      <div className="flex justify-end pt-2">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-6 py-2 text-sm font-medium text-white rounded-lg bg-primary-500 hover:bg-primary-600 disabled:bg-gray-300 glass-card-hover transition-all shadow-sm"
        >
          {saving ? '保存中...' : '保存设置'}
        </button>
      </div>
    </div>
  )
}

export default SettingsGeneral
