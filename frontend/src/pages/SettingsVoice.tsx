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
} from '../api/mimo'
import { getSpeakers } from '../api/system'

// ── Constants ──

const ENGINE_OPTIONS = [
  { value: 'mimo-tts', label: 'MiMo Cloud' },
  { value: 'edge-tts', label: 'Edge TTS' },
  { value: 'gpt-sovits', label: 'GPT-SoVITS' },
  { value: 'bert-vits2', label: 'Bert-VITS2' },
]

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
  onChange,
}: {
  engine: string
  onChange: (v: string) => void
}) {
  return (
    <section>
      <h3 className="mb-3 text-sm font-semibold text-gray-700">语音引擎</h3>
      <div className="rounded-xl border border-gray-200 bg-white/60 p-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-gray-700">当前引擎</p>
            <p className="text-xs text-gray-400">选择语音合成后端</p>
          </div>
          <div className="w-44">
            <Select
              options={ENGINE_OPTIONS}
              value={engine}
              onChange={onChange}
              placeholder="选择引擎"
            />
          </div>
        </div>
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
                  activeVoice === v.name ? 'text-primary-500' : 'text-gray-300'
                }`}
              />
              <div className="flex-1">
                <p className="text-sm font-medium">{v.displayName || v.name}</p>
                <p className="text-[10px] text-gray-400">
                  {v.gender === 'female' ? '女声' : v.gender === 'male' ? '男声' : '中性'}
                  {' · '}
                  {ENGINE_OPTIONS.find((e) => e.value === v.engine)?.label || v.engine}
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

function VoiceCloneSection() {
  const [file, setFile] = useState<File | null>(null)
  const [voiceName, setVoiceName] = useState('')
  const [cloning, setCloning] = useState(false)
  const [done, setDone] = useState(false)

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
    try {
      const fd = new FormData()
      fd.append('audio', file)
      fd.append('voice_name', voiceName)
      await mimoClone(fd)
      setDone(true)
    } catch {
      // error handled by global interceptor
    } finally {
      setCloning(false)
    }
  }, [file, voiceName])

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
            }}
            placeholder="自定义语音名称"
            className="w-full rounded-lg border border-gray-200 bg-white/60 px-3 py-2 text-sm text-gray-700 placeholder:text-gray-300 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-400/20"
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
        </div>
      </div>
    </section>
  )
}

function VoiceDesignSection() {
  const [design, setDesign] = useState<VoiceDesignRequest>({
    gender: 'female',
    age: 25,
    style: 'gentle',
    pitch: 0,
    speed: 1.0,
  })
  const [designing, setDesigning] = useState(false)

  const handleDesign = useCallback(async () => {
    setDesigning(true)
    try {
      await mimoDesign({
        voice_name: `design_${Date.now()}`,
        description: `${design.gender} voice, ${design.style} style`,
        gender: design.gender,
        age_group: design.age < 30 ? 'young' : design.age < 55 ? 'middle' : 'elder',
      })
    } catch {
      // error handled by global interceptor
    } finally {
      setDesigning(false)
    }
  }, [design])

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
              语速: {design.speed.toFixed(1)}x
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

function SynthesizeTest() {
  const [text, setText] = useState('')
  const [playing, setPlaying] = useState(false)

  const handlePlay = useCallback(async () => {
    if (!text.trim()) return
    setPlaying(true)
    try {
      await mimoSynthesize(text)
    } catch {
      // error handled by global interceptor
    } finally {
      setPlaying(false)
    }
  }, [text])

  return (
    <section>
      <h3 className="mb-3 text-sm font-semibold text-gray-700">语音合成测试</h3>
      <div className="rounded-xl border border-gray-200 bg-white/60 p-4">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="输入要合成的文本..."
          rows={3}
          className="w-full resize-none rounded-lg border border-gray-200 bg-white/60 px-3 py-2 text-sm text-gray-700 placeholder:text-gray-300 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-400/20"
        />
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
  const [engine, setEngine] = useState('mimo-tts')
  const [activeVoice, setActiveVoice] = useState('')
  const [voices, setVoices] = useState<MiMoVoice[]>([])
  const [voicesLoading, setVoicesLoading] = useState(true)

  // Fetch voices on mount & engine change
  const doFetchVoices = useCallback(async () => {
    setVoicesLoading(true)
    try {
      const [speakersRes, statusRes] = await Promise.allSettled([
        getSpeakers(engine),
        mimoStatus(),
      ])
      const list: MiMoVoice[] = []

      // Try voice/speakers first
      if (speakersRes.status === 'fulfilled') {
        const data = speakersRes.value.data ?? {}
        const raw: Record<string, unknown>[] = data.speakers ?? data.voices ?? []
        for (const r of raw) {
          list.push({
            name: (r.name as string) || (r.voice_id as string) || '',
            displayName: (r.display_name as string) || (r.name as string),
            gender: (r.gender as MiMoVoice['gender']) || undefined,
            engine: (r.engine as string) || engine,
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
  }, [engine, activeVoice])

  useEffect(() => {
    doFetchVoices()
  }, [doFetchVoices])

  const handleEngineChange = useCallback(
    async (v: string) => {
      setEngine(v)
      try {
        await mimoSetEngine(v)
      } catch {
        // error handled by global interceptor
      }
    },
    []
  )

  const handleActivate = useCallback(
    async (name: string) => {
      setActiveVoice(name)
      try {
        await mimoSwitchVoice(name)
      } catch {
        // error handled by global interceptor
      }
    },
    []
  )

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-gray-700">语音工作台</h2>
        <p className="mt-0.5 text-xs text-gray-400">
          管理语音引擎、克隆声音、设计合成参数
        </p>
      </div>

      <EngineSwitcher engine={engine} onChange={handleEngineChange} />
      <VoiceList
        voices={voices}
        activeVoice={activeVoice}
        onActivate={handleActivate}
        loading={voicesLoading}
      />
      <VoiceCloneSection />
      <VoiceDesignSection />
      <SynthesizeTest />
    </div>
  )
}
