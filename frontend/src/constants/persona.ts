/**
 * 人设维度 & 情绪配色 —— 前端唯一真源，必须与后端保持一致。
 *
 * 2026-09-19 前端审计：RoleSettingsTabs/characterBuilderStore 曾写死后端不存在的
 * speaking_style 键（liveliness/gentleness），导致真实的 expressiveness/directness
 * 裸露英文键；StatusCenter 情绪配色 10 键里只有 4 个命中后端 Emotion 枚举。
 * **改维度或情绪集时，本文件是唯一改动点**（tests/components/personaConstants.test.ts 有锁定用例）。
 *
 * 后端真源：
 * - speaking_style 四键：my_character/persona_card.py `SpeakingStyle`（formality/expressiveness/humor/directness）
 * - personality 五键：后端人格卡主维度（warmth/playfulness/independence/jealousy/stubbornness）
 * - 情绪十态：my_character/emotion_engine.py `Emotion`（LLM 输出被 prompt 限定在此集合）
 */

export const PERSONALITY_LABELS: Record<string, string> = {
  warmth: '温暖',
  playfulness: '活泼',
  independence: '独立',
  jealousy: '占有欲',
  stubbornness: '固执',
}

export const SPEAKING_STYLE_LABELS: Record<string, string> = {
  formality: '正式度',
  expressiveness: '情感表达度',
  humor: '幽默感',
  directness: '直接度',
}

/** 与 my_character/emotion_engine.py Emotion 枚举的中文值一一对应 */
export const EMOTION_COLORS: Record<string, string> = {
  开心: 'bg-macaron-yellow',
  伤心: 'bg-slate-300',
  生气: 'bg-red-300',
  撒娇: 'bg-pink-300',
  吃醋: 'bg-amber-300',
  傲娇: 'bg-rose-300',
  温柔: 'bg-macaron-mint',
  调皮: 'bg-sky-300',
  疲惫: 'bg-indigo-300',
  平常: 'bg-macaron-blue',
}

/** 新建角色的维度默认值（stubbornness=0.4 对齐后端 character_card/models.py 默认） */
export const DEFAULT_PERSONALITY: Record<string, number> = {
  warmth: 0.5,
  playfulness: 0.5,
  independence: 0.5,
  jealousy: 0.3,
  stubbornness: 0.4,
}

export const DEFAULT_SPEAKING_STYLE: Record<string, number> = {
  formality: 0.5,
  expressiveness: 0.5,
  humor: 0.5,
  directness: 0.5,
}

/** 角色存量数据可能带后端不认识的旧键（如源卡里的 liveliness/gentleness/catchphrases），
 *  表单只渲染规范键集，缺失键用默认值补齐，保存时也不会再把脏键写回后端。 */
export function normalizePersonality(style?: Record<string, unknown> | null): Record<string, number> {
  return normalizeByLabels(PERSONALITY_LABELS, DEFAULT_PERSONALITY, style)
}

export function normalizeSpeakingStyle(style?: Record<string, unknown> | null): Record<string, number> {
  return normalizeByLabels(SPEAKING_STYLE_LABELS, DEFAULT_SPEAKING_STYLE, style)
}

function normalizeByLabels(labels: Record<string, string>, defaults: Record<string, number>, style?: Record<string, unknown> | null): Record<string, number> {
  const out: Record<string, number> = {}
  for (const key of Object.keys(labels)) {
    const v = style?.[key]
    out[key] = typeof v === 'number' ? v : defaults[key]
  }
  return out
}

/** 亲密阶段兜底值 —— 与后端 shisi/emotion_stage/stage_config.py `EmotionStageConfig.default()` 一致；
 *  运行时真源是 GET /api/shisi/emotion-stage/stages（shisi.yaml 可覆盖），此处仅作为请求失败时的展示兜底。 */
export interface AffinityStage {
  name: string
  min: number
  max: number
}

export const AFFINITY_STAGES: AffinityStage[] = [
  { name: '陌生', min: 0, max: 25 },
  { name: '熟悉', min: 25, max: 50 },
  { name: '亲密', min: 50, max: 75 },
  { name: '羁绊', min: 75, max: 100 },
]
