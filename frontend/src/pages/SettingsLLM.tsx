import { useState, useEffect } from 'react'
import { Toggle } from '../components/shared'
import { config as fetchConfig, saveConfig, userLlmConfig, saveUserLlmConfig } from '../api/system'
import { useAuthStore } from '../store/authStore'

const PROVIDER_OPTIONS = [
  { value: 'auto', label: '自动回退（推荐）', desc: '按优先级依次尝试可用供应商' },
  { value: 'sensenova', label: '商汤日日新', desc: 'glm-5.2 1M上下文，强力模型，需 API Key' },
  { value: 'deepseek', label: 'DeepSeek', desc: 'DeepSeek-V2/V3，需 API Key，性价比高' },
  { value: 'zhipu', label: '智谱AI', desc: 'GLM-4.7-Flash 永久免费，无限 Token' },
  { value: 'xunfei', label: '讯飞星火', desc: 'Spark Lite 永久免费，无限 Token' },
  { value: 'baidu', label: '百度千帆', desc: 'ERNIE-Speed 每月 50 万免费 Token' },
  { value: 'opencode_zen', label: 'OpenCode Zen（兜底）', desc: '完全免费，无需 API Key，质量一般' },
  { value: 'custom', label: '自定义（OpenAI 兼容）', desc: '任意 OpenAI 兼容 API，如 Ollama、Groq、Together AI 等' },
]

const PROVIDER_GUIDE: Record<string, { apply_url: string; note: string }> = {
  sensenova: { apply_url: 'https://platform.sensenova.cn', note: '注册 → 控制台 → API Keys → 创建 sk- 密钥' },
  deepseek: { apply_url: 'https://platform.deepseek.com', note: '注册 → API Keys → 创建 Key' },
  zhipu: { apply_url: 'https://open.bigmodel.cn', note: '注册 → API 密钥 → 添加 API Key' },
  xunfei: { apply_url: 'https://console.xfyun.cn', note: '注册 → 星火大模型 → API Key' },
  baidu: { apply_url: 'https://console.bce.baidu.com', note: '注册 → 千帆大模型 → 创建应用' },
  custom: { apply_url: '', note: '填入任意 OpenAI 兼容 API 的地址、Key 和模型名，如 Ollama 本地 http://localhost:11434/v1' },
}

function SettingsLLM() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [configSource, setConfigSource] = useState<'user' | 'global'>('global')

  // 获取当前用户角色（从 zustand authStore）
  const user = useAuthStore(s => s.user)
  const isAdmin = user?.role === 'admin'

  // 核心连接参数
  const [provider, setProvider] = useState('auto')
  const [modelName, setModelName] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [apiBase, setApiBase] = useState('')

  // 生成参数
  const [temperature, setTemperature] = useState(0.85)
  const [maxTokens, setMaxTokens] = useState(2048)

  // 缓存
  const [llmCache, setLlmCache] = useState(true)
  const [cacheDuration, setCacheDuration] = useState(30)

  // 加载配置：普通用户用 /user/llm-config，admin 用全局 /config（默认）或用户级
  /* eslint-disable react-hooks/set-state-in-effect -- data fetching on mount, cancelled flag 防竞态 */
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    // 普通用户始终用用户级 API；admin 默认用全局，但也可读用户级
    const useUserApi = !isAdmin
    const fetchFn = useUserApi ? userLlmConfig : fetchConfig

    fetchFn()
      .then(res => {
        if (cancelled) return
        const data = res.data ?? {}
        // /user/llm-config 返回 { source, llm }，/config 返回顶层配置对象
        const llm = useUserApi ? (data.llm ?? {}) : (data.llm ?? {})
        const source = useUserApi ? (data.source ?? 'global') : 'global'
        setConfigSource(source)
        setProvider(llm.provider ?? 'auto')
        setModelName(llm.model || llm.primary_model || '')
        setApiKey(llm.api_key && llm.api_key !== '****' ? llm.api_key : '')
        setApiBase(llm.api_base ?? '')
        setTemperature(Number(llm.temperature) || 0.85)
        setMaxTokens(llm.max_tokens ?? 2048)
        const cache = llm.cache ?? {}
        setLlmCache(cache.enabled !== false)
        setCacheDuration(Math.max(1, Math.round((cache.ttl ?? 1800) / 60)))
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : '加载配置失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => { cancelled = true }
  }, [isAdmin])
  /* eslint-enable react-hooks/set-state-in-effect */

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    setSuccess(false)

    try {
      const llmConfig: Record<string, unknown> = {
        provider,
        model: modelName,
        primary_model: modelName,
        api_base: apiBase,
        temperature,
        max_tokens: maxTokens,
        cache: {
          enabled: llmCache,
          ttl: cacheDuration * 60,
        },
      }
      // GET 返回 ****。空输入表示保留原 key；只有新输入的值才发送。
      if (apiKey && apiKey !== '****') llmConfig.api_key = apiKey

      // 普通用户保存到用户级；admin 保存到全局（可扩展为可选）
      const saveFn = isAdmin ? saveConfig : saveUserLlmConfig
      await saveFn({ llm: llmConfig })
      setSuccess(true)
      setTimeout(() => setSuccess(false), 3000)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '保存配置失败')
    } finally {
      setSaving(false)
    }
  }

  const guide = PROVIDER_GUIDE[provider]

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[200px]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-500 border-t-transparent" />
        <span className="ml-2 text-sm text-gray-400">加载配置中...</span>
      </div>
    )
  }

  return (
    <div className="space-y-6 max-w-2xl">
      {/* 配置来源指示 */}
      <div className="text-xs text-gray-400">
        {isAdmin
          ? '当前编辑：全局默认配置（所有用户回退使用）'
          : configSource === 'user'
            ? '当前编辑：你的个人 LLM 配置（独立于其他用户）'
            : '当前使用全局默认配置，保存后将创建你的个人配置'
        }
      </div>
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

      {/* 供应商选择 */}
      <section>
        <h3 className="text-sm font-semibold text-gray-700 mb-3">LLM 供应商</h3>
        <div className="glass-card rounded-xl p-4 space-y-2">
          {PROVIDER_OPTIONS.map((opt) => (
            <label
              key={opt.value}
              className={`flex items-center gap-3 rounded-lg px-3 py-2.5 cursor-pointer transition
                ${provider === opt.value ? 'bg-primary-50 ring-1 ring-primary-200' : 'hover:bg-gray-50'}`}
            >
              <input
                type="radio"
                name="provider"
                value={opt.value}
                checked={provider === opt.value}
                onChange={(e) => setProvider(e.target.value)}
                className="accent-primary-500"
              />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-700">{opt.label}</p>
                <p className="text-xs text-gray-400 truncate">{opt.desc}</p>
              </div>
            </label>
          ))}
        </div>
      </section>

      {/* 连接参数 */}
      {provider !== 'opencode_zen' && (
        <section>
          <h3 className="text-sm font-semibold text-gray-700 mb-3">连接参数</h3>
          <div className="glass-card rounded-xl p-4 space-y-4">
              <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-700">API 地址</p>
                <p className="text-xs text-gray-400">格式: https://xxx.com/v1</p>
              </div>
              <input
                type="text"
                value={apiBase}
                onChange={(e) => setApiBase(e.target.value)}
                placeholder="https://api.deepseek.com/v1"
                className="w-56 px-3 py-1.5 text-xs input-macaron rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50"
              />
            </div>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-700">API Key</p>
                <p className="text-xs text-gray-400">从供应商后台获取</p>
              </div>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-..."
                className="w-56 px-3 py-1.5 text-xs input-macaron rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50"
              />
            </div>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-700">模型名称</p>
                <p className="text-xs text-gray-400">如 deepseek-chat, glm-4-flash</p>
              </div>
              <input
                type="text"
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
                placeholder="deepseek-chat"
                className="w-56 px-3 py-1.5 text-xs input-macaron rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50"
              />
            </div>
          </div>
          {guide && (
            <p className="mt-2 text-xs text-gray-400">
              {guide.apply_url ? (
                <>
                  申请地址: <a href={guide.apply_url} target="_blank" rel="noopener noreferrer" className="text-primary-500 hover:underline">{guide.apply_url}</a>
                  &nbsp;— {guide.note}
                </>
              ) : (
                <>{guide.note}</>
              )}
            </p>
          )}
        </section>
      )}

      {/* 生成参数 */}
      <section>
        <h3 className="text-sm font-semibold text-gray-700 mb-3">生成参数</h3>
        <div className="glass-card rounded-xl p-4 space-y-5">
          {/* 温度 */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <div>
                <p className="text-sm text-gray-700">温度</p>
                <p className="text-xs text-gray-400">生成随机性（0-1）</p>
              </div>
              <span className="text-sm font-semibold text-gray-700 tabular-nums w-10 text-right">
                {Number(temperature || 0).toFixed(2)}
              </span>
            </div>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={temperature}
              onChange={(e) => setTemperature(parseFloat(e.target.value))}
              className="w-full accent-primary-500"
            />
            <div className="flex justify-between text-[10px] text-gray-300">
              <span>0（确定）</span>
              <span>0.5（平衡）</span>
              <span>1（随机）</span>
            </div>
          </div>

          {/* 最大 Token */}
          <div className="flex items-center justify-between pt-2 border-t border-gray-100">
            <div>
              <p className="text-sm text-gray-700">最大 Token</p>
              <p className="text-xs text-gray-400">单次生成的最大 Token 数</p>
            </div>
            <input
              type="number"
              value={maxTokens}
              onChange={(e) => setMaxTokens(parseInt(e.target.value) || 2048)}
              min={256}
              max={32768}
              step={256}
              className="w-24 px-3 py-1.5 text-xs input-macaron rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50 text-center"
            />
          </div>
        </div>
      </section>

      {/* LLM 缓存 */}
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
              max={525600}
              className="w-24 px-3 py-1.5 text-xs input-macaron rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50 text-center"
            />
          </div>
        </div>
      </section>

      <div className="flex justify-end pt-4">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-6 py-2 text-sm font-medium text-white rounded-lg bg-primary-500 hover:bg-primary-600 disabled:bg-gray-300 transition-all shadow-sm"
        >
          {saving ? '保存中...' : '保存设置'}
        </button>
      </div>
    </div>
  )
}

export default SettingsLLM
