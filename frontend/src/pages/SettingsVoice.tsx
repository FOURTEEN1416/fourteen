import { useState, useCallback, useEffect } from 'react'
import { Mic, Upload, Play, Check, Loader2 } from 'lucide-react'
import { Select } from '../components/shared'
import type { MiMoVoice, VoiceDesignRequest } from '../api/mimo'
import {
  mimoClone,
  mimoDesign,
  mimoSynthesize,
  mimoSetEngine,
  mimoSwitchVoice,
  mimoStatus,
  playAudioBlob,
} from '../api/mimo'
import { getSpeakers } from '../api/system'
import { useAuthStore } from '../store/authStore'

// ── Constants ──

// W7：engine（mimo-tts）与 model（MiMo 模型名）类型分明——
// 引擎卡片仅展示（MiMo-only，无可切换项），克隆/设计前按需切换模型。
const ENGINE_OPTIONS = [
  { value: 'mimo-tts', label: 'MiMo Cloud' },
]

const MODEL_VOICECLONE = 'mimo-v2.5-tts-voiceclone'
const MODEL_VOICEDESIGN = 'mimo-v2.5-tts-voicedesign'

const GENDER_OPTIONS = [
  { value: 'female', label: '女声' },
  { value: 'male', label: '男声' },
  { value: 'neutral', label: '中性' },
]

const STYLE_OPTIONS = [
  { value: 'gentle', label: '温柔' },
  { value: 'lively', label: '活泼' },
  { value: 'professional', label: '专业' },
  { value: 'cute', label: '可爱' },
]

// ── Sub-components ──

function EngineSwitcher({
  engine,
  isAdmin,
}: {
  engine: string
  isAdmin: boolean
}) {
  return (
    <section>
      <h3 className="mb-3 text-sm font-semibold text-gray-700">语音引擎</h3>
      <div className="grid grid-cols-1 gap-2">
        {ENGINE_OPTIONS.map((opt) => (
          <div
            key={opt.value}
            className={`text-left p-3 rounded-xl transition-all ${
              engine === opt.value
                ? 'glass-yellow ring-1 ring-primary-400/30'
                : 'glass-card border border-gray-200'
            }`}
          >
            <p className={`text-sm font-medium ${engine === opt.value ? 'text-primary-700' : 'text-gray-700'}`}>{opt.label}</p>
            {/* P0 收尾：模型切换已是 admin 门能力，普通用户侧措辞如实 */}
            <p className="text-[10px] text-gray-400 mt-0.5">
              {isAdmin ? '克隆/设计音色时将自动切换对应 MiMo 模型' : '克隆/设计依赖对应 MiMo 模型，由管理员切换'}
            </p>
          </div>
        ))}
      </div>
    </section>
  )
}

function VoiceList({
  voices,
  activeVoice,
  onActivate,
  loading,
}: {
  voices: MiMoVoice[]
  activeVoice: string
  onActivate: (name: string) => void
  loading?: boolean
}) {
  return (
    <section>
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700">
        可用语音
        {loading && (
          <div className="ml-auto h-3 w-3 animate-spin rounded-full border-2 border-primary-500 border-t-transparent" />
        )}
      </h3>
      <div className="rounded-xl border border-gray-200 bg-white/60 p-4">
        {loading ? (
          <p className="py-6 text-center text-sm text-gray-400">加载中...</p>
        ) : voices.length === 0 ? (
          <p className="py-6 text-center text-sm text-gray-400">暂无可用语音</p>
        ) : (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {voices.map((v) => (
            <button
              key={v.name}
              onClick={() => onActivate(v.name)}
              className={`flex items-center gap-3 rounded-lg border px-3 py-2.5 text-left text-sm transition ${
                activeVoice === v.name
                  ? 'border-primary-300 bg-primary-50 text-primary-700'
                  : 'border-gray-100 text-gray-600 hover:border-gray-200 hover:bg-gray-50'
              }`}
            >
              <Mic
                className={`h-4 w-4 ${
                  activeVoice === v.name ? 'text-primary-500' : 'text-gray-400'
                }`}
              />
              <div className="flex-1">
                <p className="text-sm font-medium">{v.displayName || v.name}</p>
                <p className="text-[10px] text-gray-400">
                  {v.kind === 'clone' ? '克隆音色' : v.kind === 'design' ? '设计音色' : '预设'}
                  {v.description ? ` · ${v.description}` : ''}
                </p>
              </div>
              {activeVoice === v.name && (
                <Check className="h-3.5 w-3.5 text-primary-500" />
              )}
            </button>
          ))}
        </div>
      )}
      </div>
    </section>
  )
}

function VoiceCloneSection({ onCreated }: { onCreated: (voiceId: string) => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [voiceName, setVoiceName] = useState('')
  const [cloning, setCloning] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState('')
  const [createdId, setCreatedId] = useState('')
  // P0 收尾：set-engine 降级提示（琥珀色建议级，区别于 error 的失败红）
  const [engineNotice, setEngineNotice] = useState('')

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const f = e.target.files?.[0]
      if (f) setFile(f)
    },
    []
  )

  const handleClone = useCallback(async () => {
    if (!file || !voiceName.trim()) return
    setCloning(true)
    setError('')
    setEngineNotice('')
    try {
      // W7：克隆能力在 voiceclone 模型上——先切模型（传模型名，非引擎名）。
      // P0 收尾：set-engine 已是 admin 门端点，且是克隆的技术前置（后端按全局
      // provider 模型校验，见 mimo_voice_routes.clone 的 health.model 检查）。
      // 普通用户 403 时降级不阻断——平台模型恰为 voiceclone 时克隆仍可成功；
      // 不恰时由克隆自身的 catch 呈现后端 400 明细，两个信息叠加即完整因果。
      try {
        await mimoSetEngine(MODEL_VOICECLONE)
      } catch {
        setEngineNotice('音色模型切换为管理员能力；将以平台当前模型尝试克隆，若失败请联系管理员切换模型')
      }
      const fd = new FormData()
      fd.append('audio', file)
      fd.append('voice_name', voiceName)
      const res = await mimoClone(fd)
      const voiceId = (res.data as { voice_id?: string })?.voice_id || ''
      const saved = (res.data as { catalog_saved?: boolean })?.catalog_saved
      setCreatedId(voiceId)
      setDone(true)
      onCreated(voiceId)
      if (saved === false) setError('克隆成功，但本地音色目录登记失败——刷新后可能无法找回该音色')
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
      setError(detail || '克隆失败，请稍后重试')
    } finally {
      setCloning(false)
    }
  }, [file, voiceName, onCreated])

  return (
    <section>
      <h3 className="mb-3 text-sm font-semibold text-gray-700">语音克隆</h3>
      <div className="rounded-xl border border-gray-200 bg-white/60 p-4">
        <div className="space-y-3">
          <input
            type="text"
            value={voiceName}
            onChange={(e) => {
              setVoiceName(e.target.value)
              setDone(false)
              setCreatedId('')
            }}
            placeholder="自定义语音名称"
            className="w-full rounded-lg border border-gray-200 bg-white/60 px-3 py-2 text-sm text-gray-700 placeholder:text-gray-400 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-400/20"
          />

          <div className="flex items-center gap-3">
            <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-dashed border-gray-300 px-4 py-2.5 text-sm text-gray-500 transition hover:border-primary-300 hover:text-primary-500">
              <Upload className="h-4 w-4" />
              {file ? file.name : '上传参考音频'}
              <input
                type="file"
                accept="audio/*"
                onChange={handleFileChange}
                className="hidden"
              />
            </label>
            {file && (
              <span className="text-xs text-gray-400">
                {(file.size / 1024).toFixed(1)} KB
              </span>
            )}
          </div>

          <button
            onClick={handleClone}
            disabled={!file || !voiceName.trim() || cloning}
            className="flex items-center gap-1.5 rounded-lg bg-primary-500 px-4 py-2 text-xs font-medium text-white transition hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {cloning ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                克隆中...
              </>
            ) : done ? (
              <>
                <Check className="h-3.5 w-3.5" />
                克隆完成
              </>
            ) : (
              '开始克隆'
            )}
          </button>

          {done && createdId && (
            <p className="text-xs text-green-600">
              音色 ID：{createdId}（已入库，可在上方「可用语音」与角色语音设置中选择）
            </p>
          )}
          {error && <p className="text-xs text-red-500">{error}</p>}
          {/* P0 收尾：模型切换降级的建议级提示（非失败，不掩盖克隆结果） */}
          {engineNotice && <p className="text-xs text-amber-600">{engineNotice}</p>}
        </div>
      </div>
    </section>
  )
}

function VoiceDesignSection({ onCreated }: { onCreated: (voiceId: string) => void }) {
  const [design, setDesign] = useState<VoiceDesignRequest>({
    gender: 'female',
    age: 25,
    style: 'gentle',
    pitch: 0,
    speed: 1.0,
  })
  const [designing, setDesigning] = useState(false)
  const [error, setError] = useState('')
  const [createdId, setCreatedId] = useState('')
  // P0 收尾：set-engine 降级提示（同克隆流）
  const [engineNotice, setEngineNotice] = useState('')

  const handleDesign = useCallback(async () => {
    setDesigning(true)
    setError('')
    setEngineNotice('')
    try {
      // W7：设计能力在 voicedesign 模型上——先切模型（传模型名，非引擎名）。
      // P0 收尾：set-engine 是设计的技术前置（后端按全局 provider 模型校验），
      // 非 admin 403 时降级不阻断，理由同克隆流。
      try {
        await mimoSetEngine(MODEL_VOICEDESIGN)
      } catch {
        setEngineNotice('音色模型切换为管理员能力；将以平台当前模型尝试设计，若失败请联系管理员切换模型')
      }
      const res = await mimoDesign({
        voice_name: `design_${Date.now()}`,
        description: `${design.gender} voice, ${design.style} style`,
        gender: design.gender,
        age_group: design.age < 30 ? 'young' : design.age < 55 ? 'middle' : 'elder',
      })
      const voiceId = (res.data as { voice_id?: string })?.voice_id || ''
      setCreatedId(voiceId)
      onCreated(voiceId)
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
      setError(detail || '设计失败，请稍后重试')
    } finally {
      setDesigning(false)
    }
  }, [design, onCreated])

  return (
    <section>
      <h3 className="mb-3 text-sm font-semibold text-gray-700">语音设计</h3>
      <div className="rounded-xl border border-gray-200 bg-white/60 p-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {/* Gender */}
          <div>
            <label className="mb-1 block text-xs text-gray-500">性别</label>
            <Select
              options={GENDER_OPTIONS}
              value={design.gender}
              onChange={(v: string) =>
                setDesign((p) => ({ ...p, gender: v as 'male' | 'female' | 'neutral' }))
              }
              placeholder="选择性别"
            />
          </div>

          {/* Style */}
          <div>
            <label className="mb-1 block text-xs text-gray-500">风格</label>
            <Select
              options={STYLE_OPTIONS}
              value={design.style}
              onChange={(v: string) => setDesign((p) => ({ ...p, style: v }))}
              placeholder="选择风格"
            />
          </div>

          {/* Age */}
          <div>
            <label className="mb-1 block text-xs text-gray-500">
              年龄: {design.age}
            </label>
            <input
              type="range"
              min={18}
              max={80}
              value={design.age}
              onChange={(e) =>
                setDesign((p) => ({ ...p, age: Number(e.target.value) }))
              }
              className="w-full accent-primary-500"
            />
          </div>

          {/* Pitch */}
          <div>
            <label className="mb-1 block text-xs text-gray-500">
              音调: {design.pitch > 0 ? `+${design.pitch}` : design.pitch}
            </label>
            <input
              type="range"
              min={-12}
              max={12}
              value={design.pitch}
              onChange={(e) =>
                setDesign((p) => ({ ...p, pitch: Number(e.target.value) }))
              }
              className="w-full accent-primary-500"
            />
          </div>

          {/* Speed */}
          <div className="sm:col-span-2">
            <label className="mb-1 block text-xs text-gray-500">
              语速: {Number(design.speed || 0).toFixed(1)}x
            </label>
            <input
              type="range"
              min={0.5}
              max={2.0}
              step={0.1}
              value={design.speed}
              onChange={(e) =>
                setDesign((p) => ({ ...p, speed: Number(e.target.value) }))
              }
              className="w-full accent-primary-500"
            />
          </div>
        </div>

        {error && <p className="mt-3 text-xs text-red-500">{error}</p>}
        {createdId && (
          <p className="mt-3 text-xs text-green-600">
            音色 ID：{createdId}（已入库，可在上方「可用语音」中选择）
          </p>
        )}
        {/* P0 收尾：模型切换降级的建议级提示（非失败，不掩盖设计结果） */}
        {engineNotice && <p className="mt-3 text-xs text-amber-600">{engineNotice}</p>}

        <div className="mt-4 flex justify-end">
          <button
            onClick={handleDesign}
            disabled={designing}
            className="flex items-center gap-1.5 rounded-lg bg-primary-500 px-4 py-2 text-xs font-medium text-white transition hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {designing ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                生成中...
              </>
            ) : (
              '应用设计'
            )}
          </button>
        </div>
      </div>
    </section>
  )
}

function SynthesizeTest({ voiceId }: { voiceId: string }) {
  const [text, setText] = useState('')
  const [playing, setPlaying] = useState(false)
  const [error, setError] = useState('')

  const handlePlay = useCallback(async () => {
    if (!text.trim()) return
    setPlaying(true)
    setError('')
    try {
      // W7：响应是音频 Blob——创建 ObjectURL 播放并回收，失败可见
      const res = await mimoSynthesize(text, voiceId ? { voiceId } : undefined)
      await playAudioBlob(res.data as Blob)
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
      setError(detail || (err instanceof Error ? err.message : '合成失败，请稍后重试'))
    } finally {
      setPlaying(false)
    }
  }, [text, voiceId])

  return (
    <section>
      <h3 className="mb-3 text-sm font-semibold text-gray-700">语音合成测试</h3>
      <div className="rounded-xl border border-gray-200 bg-white/60 p-4">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="输入要合成的文本..."
          rows={3}
          className="w-full resize-none rounded-lg border border-gray-200 bg-white/60 px-3 py-2 text-sm text-gray-700 placeholder:text-gray-400 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-400/20"
        />
        {error && <p className="mt-2 text-xs text-red-500">{error}</p>}
        <div className="mt-3 flex justify-end">
          <button
            onClick={handlePlay}
            disabled={!text.trim() || playing}
            className="flex items-center gap-1.5 rounded-lg bg-primary-500 px-4 py-2 text-xs font-medium text-white transition hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {playing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="h-3.5 w-3.5" />
            )}
            {playing ? '播放中...' : '试听'}
          </button>
        </div>
      </div>
    </section>
  )
}

// ── Main Component ──

export default function SettingsVoice() {
  // P0 收尾：set-engine / switch-voice 已是 admin 门端点（改全局 TTS 单例，见
  // api/routers/mimo_voice_routes.py 的 require_role("admin")），普通用户调用必 403。
  // 角色真源 = authStore.user.role（与 SettingsLLM 同源读取；useAuth 只是该 store 的薄封装）。
  const user = useAuthStore((s) => s.user)
  const isAdmin = user?.role === 'admin'
  const [engine] = useState('mimo-tts')
  const [activeVoice, setActiveVoice] = useState('')
  const [voices, setVoices] = useState<MiMoVoice[]>([])
  const [voicesLoading, setVoicesLoading] = useState(true)
  const [reloadTick, setReloadTick] = useState(0)

  // Fetch voices on mount & after clone/design（catalog 是持久化 owner，刷新可找回）
  const doFetchVoices = useCallback(async () => {
    setVoicesLoading(true)
    try {
      const [speakersRes, statusRes] = await Promise.allSettled([
        getSpeakers(engine),
        mimoStatus(),
      ])
      const list: MiMoVoice[] = []

      // /voice/speakers：预设 + catalog 自定义音色（W7 起合并返回）
      if (speakersRes.status === 'fulfilled') {
        const data = speakersRes.value.data ?? {}
        const raw: Record<string, unknown>[] = data.speakers ?? data.voices ?? []
        for (const r of raw) {
          list.push({
            name: (r.name as string) || (r.voice_id as string) || '',
            displayName: (r.display_name as string) || (r.name as string),
            gender: (r.gender as MiMoVoice['gender']) || undefined,
            engine: (r.engine as string) || engine,
            kind: (r.kind as MiMoVoice['kind']) || 'preset',
            description: (r.description as string) || undefined,
          })
        }
      }

      // Fallback: try mimo/status
      if (list.length === 0 && statusRes.status === 'fulfilled') {
        const data = statusRes.value.data ?? {}
        const raw: Record<string, unknown>[] = data.voices ?? data.models ?? []
        for (const r of raw) {
          list.push({
            name: (r.name as string) || (r.model as string) || '',
            displayName: (r.display_name as string) || (r.name as string),
            engine: 'mimo-tts',
          })
        }
      }

      setVoices(list)
      if (!activeVoice && list.length > 0) setActiveVoice(list[0].name)
    } catch {
      setVoices([])
    } finally {
      setVoicesLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- activeVoice 仅用于首选项回填，不触发重拉
  }, [engine, reloadTick])

  // 挂载时拉数据
  /* eslint-disable react-hooks/set-state-in-effect -- data fetching on mount */
  useEffect(() => {
    doFetchVoices()
  }, [doFetchVoices])
  /* eslint-enable react-hooks/set-state-in-effect */

  // 克隆/设计成功 → 刷新列表（新音色立即可选、可绑定）
  const handleVoiceCreated = useCallback((_voiceId: string) => {
    setReloadTick((t) => t + 1)
  }, [])

  const handleActivate = useCallback(
    async (name: string) => {
      // 本地选中始终生效——它驱动下方「语音合成测试」的试听音色
      setActiveVoice(name)
      // P0 收尾：全局切换改全局 TTS 单例（admin 门端点），普通用户不发必 403 的请求；
      // 此处选择仅用于试听，个人音色绑定走角色设置
      if (!isAdmin) return
      try {
        await mimoSwitchVoice(name)
      } catch {
        // error handled by global interceptor
      }
    },
    [isAdmin]
  )

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-gray-700">语音工作台</h2>
        <p className="mt-0.5 text-xs text-gray-400">
          管理语音引擎、克隆声音、设计合成参数
        </p>
      </div>

      <EngineSwitcher engine={engine} isAdmin={isAdmin} />
      <VoiceList
        voices={voices}
        activeVoice={activeVoice}
        onActivate={handleActivate}
        loading={voicesLoading}
      />
      {/* P0 收尾：switch-voice 为 admin 门端点，普通用户侧如实告知边界与替代路径 */}
      {!isAdmin && (
        <p className="text-xs text-gray-400">
          音色全局切换为管理员能力，普通用户可在角色设置中绑定个人音色；此处选择仅用于试听。
        </p>
      )}
      <VoiceCloneSection onCreated={handleVoiceCreated} />
      <VoiceDesignSection onCreated={handleVoiceCreated} />
      <SynthesizeTest voiceId={activeVoice} />
    </div>
  )
}
