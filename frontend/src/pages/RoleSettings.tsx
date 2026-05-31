import { useState } from 'react'
import { useParams } from 'react-router-dom'
import Slider from '../components/shared/Slider'
import TagInput from '../components/shared/TagInput'
import Toggle from '../components/shared/Toggle'
import FileUpload from '../components/shared/FileUpload'
import ConfirmDialog from '../components/shared/ConfirmDialog'
import StorylineEditor from '../components/storyline/StorylineEditor'
import type { RoleSettingsTab } from '../types/framework'
import {
  User, Mic, MessageSquare, Database, Smile, Clock,
  Save, Trash2, Copy, Play, Pause, Check, AlertTriangle,
} from 'lucide-react'

// ═══ Constants ═══

const SUB_TABS: { key: RoleSettingsTab; label: string; icon: React.ReactNode }[] = [
  { key: 'basic', label: '基础', icon: <User className="w-3.5 h-3.5" /> },
  { key: 'voice', label: '语音', icon: <Mic className="w-3.5 h-3.5" /> },
  { key: 'message', label: '消息', icon: <MessageSquare className="w-3.5 h-3.5" /> },
  { key: 'data', label: '数据', icon: <Database className="w-3.5 h-3.5" /> },
  { key: 'stickers', label: '表情包', icon: <Smile className="w-3.5 h-3.5" /> },
  { key: 'timeline', label: '时间线', icon: <Clock className="w-3.5 h-3.5" /> },
]

const ENGINE_OPTIONS = [
  { value: 'edge-tts', label: 'Edge TTS', desc: '微软免费语音合成，Windows 内置' },
  { value: 'mimo-tts', label: 'MiMo Cloud', desc: '云端专业语音合成，支持克隆' },
  { value: 'gpt-sovits', label: 'GPT-SoVITS', desc: '本地精细化语音模型' },
  { value: 'bert-vits2', label: 'Bert-VITS2', desc: '轻量本地语音合成' },
]

const MIMO_MODELS = [
  { value: 'mimo-v2.5-tts', label: '基础合成', desc: '日常对话，情感丰富' },
  { value: 'mimo-v2.5-tts-voiceclone', label: '语音克隆', desc: '上传音频 → 克隆专属音色' },
  { value: 'mimo-v2.5-tts-voicedesign', label: '音色设计', desc: '文字描述 → 生成新音色' },
]

const EDGE_SPEAKERS = [
  { value: 'zh-CN-XiaoxiaoNeural', label: '晓晓（女·活泼）' },
  { value: 'zh-CN-XiaoyiNeural', label: '晓伊（女·温柔）' },
  { value: 'zh-CN-YunjianNeural', label: '云健（男·运动）' },
  { value: 'zh-CN-YunxiNeural', label: '云希（男·叙述）' },
  { value: 'zh-CN-YunyangNeural', label: '云扬（男·新闻）' },
  { value: 'zh-CN-XiaochenNeural', label: '晓辰（女·自然）' },
]

// ═══ Mock Character Data (replace with API call later) ═══

const MOCK_CHARACTER = {
  id: 'mock-001',
  name: '林晚星',
  description: '22岁美术系毕业生，温柔细腻、略带敏感，喜欢用画笔记录生活的点滴',
  avatar_gradient: 'from-amber-400 to-rose-500',
  personality: { warmth: 0.85, playfulness: 0.4, independence: 0.55, jealousy: 0.35, stubbornness: 0.25 },
  anchors: ['温柔', '细腻', '慢热', '共情力强', '外柔内刚'],
  speaking: { formality: 0.4, humor: 0.45, liveliness: 0.5, gentleness: 0.9 },
  catchphrases: ['嗯...让我想想', '你说的对呢', '今天画了新的画'],
  voice: { engine: 'mimo-tts', mimoModel: 'mimo-v2.5-tts' },
  message: { proactive: true, dailyLimit: 20, minInterval: 15, cooldown: 30, urgency: 0.7 },
  stats: { messages: 1247, memories: 156, lastActive: '2026-05-28 21:30', avgResponse: '1.2s' },
  rag: { vectorDocs: 1247, keywordIndex: 3892, hitRate: 98.2 },
  knowledgeDocs: ['关于我的基础信息.md', '性格设定指南.pdf', '对话风格参考.txt', '用户偏好记录.md'],
  createdAt: '2026-03-15 14:20',
  updatedAt: '2026-05-28 21:45',
}

// ═══ Section Wrapper ═══

function Section({ title, children, className = '' }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={`bg-white/60 backdrop-blur-sm border border-gray-200/60 rounded-2xl p-5 ${className}`}>
      <h3 className="text-sm font-semibold text-gray-800 mb-4 flex items-center gap-2">
        <span className="w-1 h-4 rounded-full bg-primary-400" />
        {title}
      </h3>
      {children}
    </div>
  )
}

// ═══ Tab: Basic ═══

function BasicTab() {
  const [name, setName] = useState(MOCK_CHARACTER.name)
  const [description, setDescription] = useState(MOCK_CHARACTER.description)
  const [personality, setPersonality] = useState(MOCK_CHARACTER.personality)
  const [anchors, setAnchors] = useState(MOCK_CHARACTER.anchors)
  const [speaking, setSpeaking] = useState(MOCK_CHARACTER.speaking)
  const [catchphrases, setCatchphrases] = useState(MOCK_CHARACTER.catchphrases)

  const personaJson = { name, description, personality, core_anchors: anchors, speaking_style: { ...speaking, catchphrases } }

  return (
    <div className="space-y-4">
      {/* Identity */}
      <Section title="角色身份">
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-gray-500 mb-1.5 block">角色名称</label>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-800 placeholder-gray-300 outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-400/20 transition-all"
              placeholder="给你的角色取个名字"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-gray-500 mb-1.5 block">一句话描述</label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              rows={2}
              className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-800 placeholder-gray-300 outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-400/20 transition-all resize-none"
              placeholder="描述角色的身份、性格、背景..."
            />
          </div>
        </div>
      </Section>

      {/* Personality */}
      <Section title="性格特质">
        <div className="space-y-4">
          {Object.entries(personality).map(([key, val]) => {
            const labels: Record<string, { zh: string; emoji: string }> = {
              warmth: { zh: '温暖', emoji: '☀️' },
              playfulness: { zh: '俏皮', emoji: '🎭' },
              independence: { zh: '独立', emoji: '🦅' },
              jealousy: { zh: '吃醋', emoji: '💢' },
              stubbornness: { zh: '固执', emoji: '🧱' },
            }
            const info = labels[key] || { zh: key, emoji: '' }
            return (
              <div key={key} className="flex items-center gap-4">
                <div className="w-20 shrink-0">
                  <span className="text-xs text-gray-600">{info.emoji} {info.zh}</span>
                </div>
                <div className="flex-1">
                  <Slider value={val} min={0} max={1} step={0.01} onChange={v => setPersonality(prev => ({ ...prev, [key]: v }))} />
                </div>
                <span className="w-10 text-right text-xs font-mono text-gray-400">{(val * 100).toFixed(0)}</span>
              </div>
            )
          })}
        </div>
        <div className="mt-4 pt-4 border-t border-gray-100">
          <label className="text-xs font-medium text-gray-500 mb-1.5 block">核心锚点</label>
          <TagInput tags={anchors} placeholder="输入后按回车添加..." onChange={setAnchors} />
        </div>
      </Section>

      {/* Speaking Style */}
      <Section title="说话风格">
        <div className="space-y-4">
          {Object.entries(speaking).map(([key, val]) => {
            const labels: Record<string, string> = { formality: '正式度', humor: '幽默感', liveliness: '活泼度', gentleness: '温柔度' }
            return (
              <div key={key} className="flex items-center gap-4">
                <div className="w-20 shrink-0"><span className="text-xs text-gray-600">{labels[key] || key}</span></div>
                <div className="flex-1">
                  <Slider value={val} min={0} max={1} step={0.01} onChange={v => setSpeaking(prev => ({ ...prev, [key]: v }))} />
                </div>
                <span className="w-10 text-right text-xs font-mono text-gray-400">{(val * 100).toFixed(0)}</span>
              </div>
            )
          })}
        </div>
        <div className="mt-4 pt-4 border-t border-gray-100">
          <label className="text-xs font-medium text-gray-500 mb-1.5 block">口头禅</label>
          <TagInput tags={catchphrases} placeholder="输入后按回车添加..." onChange={setCatchphrases} />
        </div>
      </Section>

      {/* Preview */}
      <Section title="配置预览">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-gray-400">JSON 预览 · 可复制导出</span>
          <button onClick={() => navigator.clipboard.writeText(JSON.stringify(personaJson, null, 2))} className="flex items-center gap-1 text-xs text-primary-500 hover:text-primary-600 transition-colors">
            <Copy className="w-3 h-3" /> 复制
          </button>
        </div>
        <pre className="bg-gray-50 rounded-xl p-3 text-xs text-gray-600 font-mono overflow-auto max-h-48 border border-gray-100">
          {JSON.stringify(personaJson, null, 2)}
        </pre>
      </Section>

      <button className="w-full py-2.5 rounded-xl bg-primary-500 text-white text-sm font-medium hover:bg-primary-600 active:scale-[0.98] transition-all flex items-center justify-center gap-2">
        <Save className="w-4 h-4" /> 保存设置
      </button>
    </div>
  )
}

// ═══ Tab: Voice ═══

function VoiceTab() {
  const [engine, setEngine] = useState(MOCK_CHARACTER.voice.engine)
  const [mimoModel, setMimoModel] = useState(MOCK_CHARACTER.voice.mimoModel)
  const [edgeSpeaker, setEdgeSpeaker] = useState('zh-CN-XiaoxiaoNeural')
  const [edgeRate, setEdgeRate] = useState(1.0)
  const [edgePitch, setEdgePitch] = useState(0.6)
  const [voiceName, setVoiceName] = useState('')
  const [voiceDesc, setVoiceDesc] = useState('')
  const [voiceId, setVoiceId] = useState('')
  const [status, setStatus] = useState('就绪')

  return (
    <div className="space-y-4">
      {/* Engine Picker */}
      <Section title="语音引擎">
        <div className="grid grid-cols-2 gap-2">
          {ENGINE_OPTIONS.map(opt => (
            <button
              key={opt.value}
              onClick={() => setEngine(opt.value)}
              className={`text-left p-3 rounded-xl border transition-all ${
                engine === opt.value
                  ? 'border-primary-400 bg-primary-50/50 ring-1 ring-primary-400/30'
                  : 'border-gray-200 bg-white hover:border-gray-300'
              }`}
            >
              <p className={`text-sm font-medium ${engine === opt.value ? 'text-primary-700' : 'text-gray-700'}`}>{opt.label}</p>
              <p className="text-[11px] text-gray-400 mt-0.5">{opt.desc}</p>
            </button>
          ))}
        </div>
      </Section>

      {/* Edge TTS config */}
      {engine === 'edge-tts' && (
        <Section title="Edge TTS 参数">
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1.5 block">发音人</label>
              <select value={edgeSpeaker} onChange={e => setEdgeSpeaker(e.target.value)} className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-700 outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-400/20">
                {EDGE_SPEAKERS.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
            <div className="flex items-center gap-4">
              <div className="w-20 shrink-0"><span className="text-xs text-gray-600">语速</span></div>
              <div className="flex-1"><Slider value={edgeRate} min={0.5} max={2.0} step={0.1} onChange={setEdgeRate} /></div>
              <span className="w-10 text-right text-xs font-mono text-gray-400">{edgeRate.toFixed(1)}x</span>
            </div>
            <div className="flex items-center gap-4">
              <div className="w-20 shrink-0"><span className="text-xs text-gray-600">音调</span></div>
              <div className="flex-1"><Slider value={edgePitch} min={0} max={1} step={0.01} onChange={setEdgePitch} /></div>
              <span className="w-10 text-right text-xs font-mono text-gray-400">{(edgePitch * 100).toFixed(0)}</span>
            </div>
            <div className="flex justify-end gap-2">
              <button className="px-4 py-1.5 text-xs font-medium text-gray-600 rounded-lg bg-gray-100 hover:bg-gray-200 transition-colors" onClick={() => setStatus('已试听')}>
                <Play className="w-3 h-3 inline mr-1" />试听
              </button>
            </div>
          </div>
        </Section>
      )}

      {/* GPT-SoVITS */}
      {engine === 'gpt-sovits' && (
        <Section title="GPT-SoVITS 参数">
          <div className="space-y-3">
            <input className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-400/20" placeholder="服务地址 (http://localhost:5000)" />
            <input className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-400/20" placeholder="参考文本" />
            <input className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-400/20" placeholder="说话人名称" />
            <FileUpload accept=".wav,.mp3" maxSize={10} onUpload={() => {}} />
          </div>
        </Section>
      )}

      {/* Bert-VITS2 */}
      {engine === 'bert-vits2' && (
        <Section title="Bert-VITS2 参数">
          <div className="space-y-3">
            <input className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-400/20" placeholder="服务地址 (http://localhost:5000)" />
            <input className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-400/20" placeholder="说话人名称" />
            <input type="password" className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-400/20" placeholder="API Token" />
          </div>
        </Section>
      )}

      {/* MiMo Cloud */}
      {engine === 'mimo-tts' && (
        <Section title="MiMo Cloud TTS">
          <div className="space-y-3">
            <div className="grid grid-cols-3 gap-2">
              {MIMO_MODELS.map(m => (
                <button
                  key={m.value}
                  onClick={() => setMimoModel(m.value)}
                  className={`text-left p-3 rounded-xl border transition-all ${
                    mimoModel === m.value ? 'border-primary-400 bg-primary-50/50 ring-1 ring-primary-400/30' : 'border-gray-200 bg-white hover:border-gray-300'
                  }`}
                >
                  <p className="text-xs font-medium text-gray-700">{m.label}</p>
                  <p className="text-[10px] text-gray-400 mt-0.5">{m.desc}</p>
                </button>
              ))}
            </div>

            {mimoModel === 'mimo-v2.5-tts-voiceclone' && (
              <div className="bg-gray-50 rounded-xl p-4 space-y-3">
                <p className="text-xs font-medium text-gray-600">🎤 语音克隆</p>
                <input value={voiceName} onChange={e => setVoiceName(e.target.value)} className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-primary-400" placeholder="音色名称（如：林晚星·温柔版）" />
                <textarea value={voiceDesc} onChange={e => setVoiceDesc(e.target.value)} rows={2} className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-primary-400 resize-none" placeholder="音色描述（可选）" />
                <div className="border-2 border-dashed border-gray-300 rounded-xl p-4 text-center hover:border-primary-300 transition-colors cursor-pointer">
                  <Mic className="w-6 h-6 text-gray-300 mx-auto" />
                  <p className="text-xs text-gray-400 mt-1">上传 10-30 秒参考音频</p>
                </div>
                <button onClick={() => { setVoiceId('voice_clone_abc123'); setStatus('克隆成功') }} className="w-full py-2 rounded-xl bg-primary-500 text-white text-xs font-medium hover:bg-primary-600 transition-colors">开始克隆</button>
              </div>
            )}

            {mimoModel === 'mimo-v2.5-tts-voicedesign' && (
              <div className="bg-gray-50 rounded-xl p-4 space-y-3">
                <p className="text-xs font-medium text-gray-600">✨ 音色设计</p>
                <input value={voiceName} onChange={e => setVoiceName(e.target.value)} className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-primary-400" placeholder="音色名称" />
                <textarea value={voiceDesc} onChange={e => setVoiceDesc(e.target.value)} rows={3} className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-primary-400 resize-none" placeholder="描述想要的音色（如：温柔的年轻女声，带一点磁性，适合读诗）" />
                <div className="flex gap-2">
                  <select className="flex-1 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm outline-none">
                    <option value="">性别不限</option><option value="female">女声</option><option value="male">男声</option>
                  </select>
                  <select className="flex-1 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm outline-none">
                    <option value="">年龄段不限</option><option value="young">青年</option><option value="adult">成年</option>
                  </select>
                </div>
                <button onClick={() => { setVoiceId('voice_design_xyz789'); setStatus('设计成功') }} className="w-full py-2 rounded-xl bg-primary-500 text-white text-xs font-medium hover:bg-primary-600 transition-colors">开始设计</button>
              </div>
            )}

            {voiceId && (
              <div className="flex items-center justify-between bg-green-50 rounded-xl px-4 py-2.5 border border-green-200">
                <span className="text-xs text-green-700"><Check className="w-3 h-3 inline mr-1" />音色ID: {voiceId}</span>
                <button className="px-3 py-1 text-[10px] font-medium text-primary-600 bg-primary-100 rounded-lg hover:bg-primary-200">应用</button>
              </div>
            )}

            <div className="text-xs text-gray-400">状态: {status}</div>
          </div>
        </Section>
      )}

      <button className="w-full py-2.5 rounded-xl bg-primary-500 text-white text-sm font-medium hover:bg-primary-600 active:scale-[0.98] transition-all flex items-center justify-center gap-2">
        <Save className="w-4 h-4" /> 保存语音设置
      </button>
    </div>
  )
}

// ═══ Tab: Message ═══

function MessageTab() {
  const [proactive, setProactive] = useState(MOCK_CHARACTER.message.proactive)
  const [dailyLimit, setDailyLimit] = useState(MOCK_CHARACTER.message.dailyLimit)
  const [minInterval, setMinInterval] = useState(MOCK_CHARACTER.message.minInterval)
  const [cooldown, setCooldown] = useState(MOCK_CHARACTER.message.cooldown)
  const [urgency, setUrgency] = useState(MOCK_CHARACTER.message.urgency)

  return (
    <div className="space-y-4">
      {/* Status */}
      <Section title="今日发信统计">
        <div className="grid grid-cols-3 gap-4">
          {[
            { label: '已发送', value: '12 条', color: 'text-blue-600' },
            { label: '触发次数', value: '8 次', color: 'text-green-600' },
            { label: '最后发送', value: '14:32:18', color: 'text-gray-600' },
          ].map(s => (
            <div key={s.label} className="text-center p-3 rounded-xl bg-gray-50">
              <p className={`text-lg font-bold ${s.color}`}>{s.value}</p>
              <p className="text-[11px] text-gray-400 mt-0.5">{s.label}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* Toggle */}
      <Section title="主动对话">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-gray-700">允许角色主动发起对话</p>
            <p className="text-xs text-gray-400 mt-0.5">角色会在合适的时机主动搭话，如早安问候、事件提醒</p>
          </div>
          <Toggle checked={proactive} onChange={setProactive} />
        </div>
      </Section>

      {/* Frequency */}
      <Section title="频率控制">
        <div className={`space-y-4 ${!proactive ? 'opacity-40 pointer-events-none' : ''}`}>
          {[
            { label: '每日上限', value: dailyLimit, min: 1, max: 50, unit: '条/天', onChange: setDailyLimit },
            { label: '最小间隔', value: minInterval, min: 5, max: 120, unit: '分钟', onChange: setMinInterval },
            { label: '冷却时间', value: cooldown, min: 5, max: 240, unit: '分钟', onChange: setCooldown },
          ].map(s => (
            <div key={s.label} className="flex items-center gap-4">
              <div className="w-24 shrink-0"><span className="text-xs text-gray-600">{s.label}</span></div>
              <div className="flex-1"><Slider value={s.value} min={s.min} max={s.max} step={1} onChange={s.onChange} /></div>
              <span className="w-16 text-right text-xs font-mono text-gray-400">{s.value} {s.unit}</span>
            </div>
          ))}
          <div className="flex items-center gap-4">
            <div className="w-24 shrink-0"><span className="text-xs text-gray-600">紧迫阈值</span></div>
            <div className="flex-1"><Slider value={urgency} min={0} max={1} step={0.01} onChange={setUrgency} /></div>
            <span className="w-16 text-right text-xs font-mono text-gray-400">{(urgency * 100).toFixed(0)}%</span>
          </div>
        </div>
      </Section>

      <button className="w-full py-2.5 rounded-xl bg-primary-500 text-white text-sm font-medium hover:bg-primary-600 active:scale-[0.98] transition-all flex items-center justify-center gap-2">
        <Save className="w-4 h-4" /> 保存消息设置
      </button>
    </div>
  )
}

// ═══ Tab: Data ═══

function DataTab() {
  const [showDelete, setShowDelete] = useState(false)

  return (
    <div className="space-y-4">
      {/* Stats */}
      <Section title="数据概览">
        <div className="grid grid-cols-3 gap-4">
          {[
            { label: '消息总数', value: MOCK_CHARACTER.stats.messages.toLocaleString(), icon: '💬' },
            { label: '记忆条数', value: MOCK_CHARACTER.stats.memories.toLocaleString(), icon: '🧠' },
            { label: '平均响应', value: MOCK_CHARACTER.stats.avgResponse, icon: '⚡' },
          ].map(s => (
            <div key={s.label} className="text-center p-3 rounded-xl bg-gray-50">
              <p className="text-lg mb-0.5">{s.icon}</p>
              <p className="text-lg font-bold text-gray-800">{s.value}</p>
              <p className="text-[11px] text-gray-400 mt-0.5">{s.label}</p>
            </div>
          ))}
        </div>
        <div className="flex gap-2 mt-4">
          <button className="flex-1 py-2 rounded-lg bg-gray-100 text-xs font-medium text-gray-600 hover:bg-gray-200 transition-colors">导出 JSON</button>
          <button className="flex-1 py-2 rounded-lg bg-gray-100 text-xs font-medium text-gray-600 hover:bg-gray-200 transition-colors">导出 CSV</button>
        </div>
      </Section>

      {/* RAG */}
      <Section title="知识库引擎 (RAG)">
        <div className="flex items-center gap-2 mb-4">
          <span className="flex items-center gap-1 text-[11px] text-green-600 font-medium">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" /> 运行正常
          </span>
        </div>
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: '向量文档', value: MOCK_CHARACTER.rag.vectorDocs.toLocaleString() },
            { label: '关键词索引', value: MOCK_CHARACTER.rag.keywordIndex.toLocaleString() },
            { label: '检索命中率', value: `${MOCK_CHARACTER.rag.hitRate}%` },
          ].map(s => (
            <div key={s.label} className="text-center p-2.5 rounded-xl bg-purple-50/50 border border-purple-100/50">
              <p className="text-lg font-bold text-purple-600">{s.value}</p>
              <p className="text-[10px] text-gray-400 mt-0.5">{s.label}</p>
            </div>
          ))}
        </div>
        <div className="flex gap-2 mt-3">
          <input className="flex-1 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-primary-400" placeholder="搜索知识库..." />
          <button className="px-4 py-2 rounded-xl bg-primary-500 text-white text-xs font-medium hover:bg-primary-600 transition-colors">搜索</button>
        </div>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {MOCK_CHARACTER.knowledgeDocs.map(doc => (
            <span key={doc} className="inline-flex items-center gap-1 rounded-lg bg-gray-100 px-2.5 py-1 text-[11px] text-gray-600">
              📄 {doc}
              <button className="ml-1 text-gray-400 hover:text-red-400">×</button>
            </span>
          ))}
        </div>
        <div className="mt-3 border-2 border-dashed border-gray-200 rounded-xl p-4 text-center hover:border-primary-300 transition-colors cursor-pointer">
          <p className="text-xs text-gray-400">拖拽文件上传 · .txt .pdf .md ≤ 10MB</p>
        </div>
      </Section>

      {/* Timestamps */}
      <Section title="时间信息">
        <div className="flex justify-between text-sm">
          <div><span className="text-gray-400 text-xs">创建时间</span><p className="text-gray-700 font-medium">{MOCK_CHARACTER.createdAt}</p></div>
          <div><span className="text-gray-400 text-xs">最后更新</span><p className="text-gray-700 font-medium">{MOCK_CHARACTER.updatedAt}</p></div>
        </div>
      </Section>

      {/* Danger */}
      <Section title="危险区域" className="border-red-200/40 bg-red-50/20">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-red-600">删除角色</p>
            <p className="text-xs text-gray-400 mt-0.5">所有对话记录、记忆、知识文档将被永久清除</p>
          </div>
          <button onClick={() => setShowDelete(true)} className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-red-500 text-white text-xs font-medium hover:bg-red-600 transition-colors">
            <Trash2 className="w-3.5 h-3.5" /> 删除
          </button>
        </div>
      </Section>

      <ConfirmDialog open={showDelete} title="确认删除角色" message={`确定要删除「${MOCK_CHARACTER.name}」吗？此操作不可恢复。`} confirmText="确认删除" cancelText="取消" variant="danger" onConfirm={() => setShowDelete(false)} onCancel={() => setShowDelete(false)} />
    </div>
  )
}

// ═══ Tab: Stickers ═══

function StickersTab() {
  const EMOJIS = ['😊', '😘', '🥰', '😭', '😤', '🤔', '💕', '✨', '🎉', '😅', '😂', '🥺', '😍', '🙈', '💪', '🔥', '👍', '👋']

  return (
    <div className="space-y-4">
      <Section title="常用表情">
        <div className="grid grid-cols-6 gap-2">
          {EMOJIS.map(emoji => (
            <button key={emoji} className="aspect-square rounded-xl bg-white border border-gray-100 text-2xl flex items-center justify-center hover:bg-primary-50 hover:border-primary-200 hover:scale-110 transition-all active:scale-95">
              {emoji}
            </button>
          ))}
        </div>
      </Section>
      <Section title="自定义贴图">
        <div className="border-2 border-dashed border-gray-200 rounded-xl p-6 text-center hover:border-primary-300 transition-colors cursor-pointer">
          <Smile className="w-8 h-8 text-gray-300 mx-auto" />
          <p className="text-xs text-gray-400 mt-2">上传自定义贴图</p>
          <p className="text-[10px] text-gray-300 mt-0.5">PNG / GIF / JPEG · 每张 ≤ 5MB</p>
        </div>
      </Section>
      <button className="w-full py-2.5 rounded-xl bg-primary-500 text-white text-sm font-medium hover:bg-primary-600 active:scale-[0.98] transition-all flex items-center justify-center gap-2">
        <Save className="w-4 h-4" /> 保存表情包设置
      </button>
    </div>
  )
}

// ═══ Tab: Timeline ═══

function TimelineTab() {
  return (
    <div className="space-y-4">
      <StorylineEditor characterId="mock-001" />
      <button className="w-full py-2.5 rounded-xl bg-primary-500 text-white text-sm font-medium hover:bg-primary-600 active:scale-[0.98] transition-all flex items-center justify-center gap-2">
        <Save className="w-4 h-4" /> 保存时间线
      </button>
    </div>
  )
}

// ═══ Main Component ═══

function ActiveTab({ tab }: { tab: RoleSettingsTab }) {
  switch (tab) {
    case 'basic': return <BasicTab />
    case 'voice': return <VoiceTab />
    case 'message': return <MessageTab />
    case 'data': return <DataTab />
    case 'stickers': return <StickersTab />
    case 'timeline': return <TimelineTab />
  }
}

export default function RoleSettings() {
  const { userId, roleId } = useParams<{ userId: string; roleId: string }>()
  const [activeTab, setActiveTab] = useState<RoleSettingsTab>('basic')

  // Decode roleId for display
  const displayId = roleId ? decodeURIComponent(roleId) : ''

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-6">
        {/* ── Character Header ── */}
        <div className="bg-white/70 backdrop-blur-sm border border-gray-200/60 rounded-2xl p-5 mb-5">
          <div className="flex items-center gap-4">
            {/* Avatar */}
            <div className={`w-14 h-14 rounded-2xl bg-gradient-to-br ${MOCK_CHARACTER.avatar_gradient} flex items-center justify-center text-white text-xl font-bold shadow-sm shrink-0`}>
              {MOCK_CHARACTER.name[0]}
            </div>
            <div className="min-w-0 flex-1">
              <h1 className="text-lg font-bold text-gray-800">{MOCK_CHARACTER.name}</h1>
              <p className="text-sm text-gray-500 truncate">{MOCK_CHARACTER.description}</p>
              <div className="flex items-center gap-3 mt-1.5">
                <span className="text-[11px] text-gray-400">ID: {displayId || roleId || 'mock-001'}</span>
                <span className="text-[11px] text-gray-400">用户: {userId || 'default'}</span>
                <span className="flex items-center gap-1 text-[11px] text-green-600">
                  <span className="w-1.5 h-1.5 rounded-full bg-green-400" /> 活跃
                </span>
              </div>
            </div>
            <div className="text-right shrink-0">
              <p className="text-xs text-gray-400">最后活跃</p>
              <p className="text-sm font-medium text-gray-700">{MOCK_CHARACTER.stats.lastActive.split(' ')[0]}</p>
            </div>
          </div>
        </div>

        {/* ── Tabs ── */}
        <div className="flex gap-1 p-1 bg-gray-100/60 rounded-2xl mb-5">
          {SUB_TABS.map(t => (
            <button
              key={t.key}
              onClick={() => setActiveTab(t.key)}
              className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-2.5 text-sm font-medium rounded-xl transition-all duration-200 ${
                activeTab === t.key
                  ? 'bg-white text-primary-700 shadow-sm'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </div>

        {/* ── Tab Content ── */}
        <ActiveTab tab={activeTab} />
      </div>
    </div>
  )
}
