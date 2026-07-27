import { User, Mic, MessageSquare, Database, Smile, Clock } from 'lucide-react'
import type { RoleSettingsTab } from '../../types/framework'

export const SUB_TABS: { key: RoleSettingsTab; label: string; icon: React.ReactNode }[] = [
  { key: 'basic', label: '基础', icon: <User className="w-3.5 h-3.5" /> },
  { key: 'voice', label: '语音', icon: <Mic className="w-3.5 h-3.5" /> },
  { key: 'message', label: '消息', icon: <MessageSquare className="w-3.5 h-3.5" /> },
  { key: 'data', label: '数据', icon: <Database className="w-3.5 h-3.5" /> },
  { key: 'stickers', label: '表情包', icon: <Smile className="w-3.5 h-3.5" /> },
  { key: 'timeline', label: '时间线', icon: <Clock className="w-3.5 h-3.5" /> },
]

export const ENGINE_OPTIONS = [
  { value: 'mimo-tts', label: 'MiMo Cloud', desc: '云端专业语音合成，支持克隆' },
]

export const MIMO_MODELS = [
  { value: 'mimo-v2.5-tts', label: '基础合成', desc: '日常对话，情感丰富' },
  { value: 'mimo-v2.5-tts-voiceclone', label: '语音克隆', desc: '上传音频 → 克隆专属音色' },
  { value: 'mimo-v2.5-tts-voicedesign', label: '音色设计', desc: '文字描述 → 生成新音色' },
]
