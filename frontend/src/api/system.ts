import client from './client'

// ── Health & Stats ──

export function health() { return client.get('/health') }
export function stats() { return client.get('/stats') }
export function dashboardStats() { return client.get('/stats/dashboard') }

// ── Config ──

export function config() { return client.get('/config') }
export function saveConfig(cfg: Record<string, unknown>) { return client.post('/config', { config: cfg }) }

// ── 用户级 LLM 配置（多用户 API Key 隔离） ──

export function userLlmConfig() { return client.get('/user/llm-config') }
export function saveUserLlmConfig(cfg: Record<string, unknown>) { return client.post('/user/llm-config', { config: cfg }) }

// ── Persona ──

export function personaProfile() { return client.get('/persona/profile') }
export function personaEvolutionLog(limit = 50) { return client.get('/persona/evolution-log', { params: { limit } }) }

// ── Memory Facts ──

export function memoryFacts(category = '', limit = 50) {
  return client.get('/memory/facts', { params: { category, limit } })
}

// ── Tools & Proactive ──

export function tools() { return client.get('/tools') }
export function toolsHealth() { return client.get('/tools/health') }
export function toggleTool(name: string, enabled: boolean) { return client.post(`/tools/${name}/toggle`, { enabled }) }
export function toolHistory(limit = 50) { return client.get('/tools/history', { params: { limit } }) }
export function proactiveHistory(limit = 50) { return client.get('/proactive/history', { params: { limit } }) }
export function proactiveGetConfig() { return client.get('/proactive/config') }
export function proactiveSend(messageType?: string, sessionKey?: string) {
  // W11-D4/契约对齐：POST /api/proactive/send 的后端规则是「目标显式优先；
  // 仅一路会话时可省略；多路会话必须显式指定 session_key（否则 400 拒猜）」。
  // 旧实现永不发送 session_key ⇒ 多会话部署下必然 400，且失败形态像"发送成功"。
  const body: { message_type?: string; session_key?: string } = {}
  if (messageType) body.message_type = messageType
  if (sessionKey) body.session_key = sessionKey
  return client.post('/proactive/send', body)
}
export function proactivePause(paused: boolean) { return client.post('/proactive/pause', { paused }) }
export function updateProactiveConfig(cfg: {
  threshold?: number
  max_daily?: number
  min_interval_minutes?: number
  cooldown_after_reply_minutes?: number
  quiet_hours_start?: number
  quiet_hours_end?: number
  reply_mode?: 'immersive' | 'novel'
  /** 2026-10-21 R7：作用域。global=全站默认（旧行为）；user=仅目标 user_key */
  reply_mode_scope?: 'global' | 'user'
  /** scope=user 时的目标会话键（完整形态 `N:wxid`） */
  user_key?: string
  llm_proactive_enabled?: boolean
  llm_proactive_style_hint?: string
  llm_proactive_intensity?: 'low' | 'normal' | 'high'
  llm_proactive_respect_quiet?: boolean
  llm_proactive_character_hint?: string
  /** T-4：主动等待窗随机抖动上限（秒）；0=关闭 */
  llm_proactive_jitter_seconds?: number
  /** 零 token 骰子闸（2026-10-28 dsh-speak-up）：10 键 partial merge */
  dice_gate?: DiceGateConfigWrite
}) {
  return client.post('/proactive/config', cfg)
}

/**
 * 骰子闸可写字段（与后端 `DiceGateConfigRequest` / `frequency.DICE_GATE_DEFAULTS`
 * 逐键一致）。缺省的键=不改；写侧服务端还会校验越界（422）与
 * `idle_long_minutes > idle_short_minutes`（400）。
 */
export interface DiceGateConfigWrite {
  enabled?: boolean
  dry_run?: boolean
  /** 峰时（工作日 09–12 / 14–18）命中线，% */
  peak_line?: number
  /** 谷时命中线，% */
  offpeak_line?: number
  /** 连没中每次加成，% */
  miss_bonus?: number
  /** 连没中加成上限，% */
  miss_bonus_cap?: number
  /** 短空闲阈值（分钟） */
  idle_short_minutes?: number
  /** 长空闲阈值（分钟），必须 > idle_short_minutes */
  idle_long_minutes?: number
  /** 短空闲命中线乘数 */
  idle_short_factor?: number
  /** 长空闲命中线乘数 */
  idle_long_factor?: number
}

// ── 用户自助回复模式（2026-10-21 R7） ──
// 为什么不复用 /proactive/config：那是 admin 门 + `orch._ase` 503 门；
// 普通用户既无 admin 角色，也不该被「主动引擎是否初始化」耦合。
// 后端从 JWT 派生 user_key（绝不接受客户端传 user_key）。
export function getMyReplyMode() { return client.get('/user/reply-mode') }
export function setMyReplyMode(replyMode: 'immersive' | 'novel') {
  return client.post('/user/reply-mode', { reply_mode: replyMode })
}

// ── Humanize（拟人度：段间延时 / 主动拆条） ──
// 真源 = data/scheduler_config.json 的 `humanize` 块（与 reply_mode / quiet_hours /
// llm_proactive 同一份跨 worker 运行时配置）。后端 api/routers/training_routes.py
// 的 GET/POST|PUT /api/humanize/config：admin 门 + 纯文件热读（**无** 503，
// 与 /api/proactive/config 刻意不同：4 worker 里只有 master 持有调度器）。
//
// 边界纪律：humanize **不塞进** /api/proactive/config 响应（避免一个配置文件两个
// owner 的前端投影互相覆盖），前端分别拉取（见 HumanizeSection / humanizeStore）。

/** 段间延时档位（唯一真源在 store/humanizeStore.ts；FF-0007：store 禁止 import API，故反向） */
export type { HumanizeDelayMode, HumanizeConfigPayload } from '../store/humanizeStore'
import type { HumanizeConfigPayload } from '../store/humanizeStore'

export function humanizeGetConfig(userKey?: string) {
  return userKey
    ? client.get('/humanize/config', { params: { user_key: userKey } })
    : client.get('/humanize/config')
}
/** partial merge：只传要改的键（后端在 json_state 锁内 merge，未传键保持原值）；
 *  userKey 非空 → 只写该用户的 user_humanize 覆盖行（admin 作用域） */
export function updateHumanizeConfig(cfg: HumanizeConfigPayload, userKey?: string) {
  return client.post('/humanize/config', userKey ? { ...cfg, user_key: userKey } : cfg)
}
/** 清除该用户的拟人度覆盖（回落全局默认） */
export function clearHumanizeUserOverride(userKey: string) {
  return client.delete('/humanize/config', { params: { user_key: userKey } })
}

// ── 话题状态机（管理端只读快照 + 配置热读写） ──
// 真源 = data/scheduler_config.json 的 `topic_state` 块；后端
// api/routers/topic_routes.py 的三个端点（admin 门，无 503）：
//   GET  /api/topic-state/config
//   POST /api/topic-state/config  （partial merge）
//   GET  /api/topic-state/{session_key}?character_id=...
import type { TopicStateConfigPayload } from '../store/topicStateStore'

export function topicStateGetConfig() {
  return client.get('/topic-state/config')
}
export function updateTopicStateConfig(cfg: TopicStateConfigPayload) {
  return client.post('/topic-state/config', cfg)
}
export function topicStateSnapshot(sessionKey: string, characterId = 'default') {
  return client.get(`/topic-state/${encodeURIComponent(sessionKey)}`, {
    params: { character_id: characterId },
  })
}

export function agentPlaneReplay(sessionKey: string, turnId?: string, replyId?: string) {
  return client.get('/agent-plane/replay', { params: { session_key: sessionKey, turn_id: turnId || '', reply_id: replyId || '' } })
}
export function agentPlaneProfile(sessionKey: string) {
  return client.get('/agent-plane/profile', { params: { session_key: sessionKey } })
}
export function agentPlaneEvents(sessionKey: string, eventType?: string, limit = 50) {
  return client.get('/agent-plane/events', { params: { session_key: sessionKey, event_type: eventType || '', limit } })
}
export function agentPlaneCurate(sessionKey?: string) {
  return client.post('/agent-plane/curate', null, { params: sessionKey ? { session_key: sessionKey } : {} })
}
export function agentPlaneProbes(sessionKey?: string) {
  return client.get('/agent-plane/probes', { params: sessionKey ? { session_key: sessionKey } : {} })
}

// ── Knowledge collect (Vault) ──

export function knowledgeCollectConfig() { return client.get('/knowledge/collect-config') }
export function updateKnowledgeCollectConfig(cfg: { enabled?: boolean; interval_minutes?: number }) {
  return client.post('/knowledge/collect-config', cfg)
}

// ── Logs ──

export function logs(params?: { limit?: number; level?: string; search?: string }) {
  return client.get('/logs', { params })
}

// ── Channels / WeChat ──

export function channels() { return client.get('/channels') }
// 微信通道：2026-09-19 起改为每人独立通道
// 用户侧主接口 = /api/wechat/channel*；旧 /channels/wechat/* 需 JWT 且仅返回自己的状态
export function wechatStatus() { return client.get('/wechat/channel') }
export function wechatChannelList() { return client.get('/wechat/channel/list') }
export function wechatReconnect() { return client.post('/wechat/channel/reconnect') }
export function wechatConnect() { return client.post('/wechat/channel/connect') }
export function wechatDisconnect() { return client.post('/wechat/channel/disconnect') }
export function wechatConnectionStatus() { return client.get('/wechat/channel') }
export function wechatQrCode() { return client.get('/wechat/channel/qrcode') }

// ── Psychology Profile ──
// 画像按 scope 存（生成侧键 = `{character_id}:{session_id}`），控制面必须带
// character_id 读/清，否则会读到与页面标题「角色 X 对你的理解」不同源的旧数据。

export function psychProfile(characterId?: string) {
  return client.get('/psych/profile', { params: { character_id: characterId } })
}
export function psychSnapshots(limit = 20, characterId?: string) {
  return client.get('/psych/snapshots', { params: { limit, character_id: characterId } })
}
export function psychReset(characterId?: string) {
  return client.delete('/psych/profile', { params: { character_id: characterId } })
}
export function psychMentalHealth(characterId?: string) {
  return client.get('/psych/mental-health', { params: { character_id: characterId } })
}
export function psychLiwc(characterId?: string) {
  return client.get('/psych/liwc', { params: { character_id: characterId } })
}

// ── Safety Panel ──

export function safetyStats() { return client.get('/safety/stats') }
export function safetyLog(limit = 50) { return client.get('/safety/log', { params: { limit } }) }
export function safetyConfig(enabled: boolean) { return client.post('/safety/config', null, { params: { enabled } }) }

// ── RAG Knowledge Base ──

export function ragStats() { return client.get('/rag/stats') }
export function ragSearch(query: string, topK = 5) { return client.post('/rag/search', null, { params: { query, top_k: topK } }) }
export function ragUpload(file: File) {
  const form = new FormData()
  form.append('file', file)
  return client.post('/rag/documents', form, { headers: { 'Content-Type': 'multipart/form-data' } })
}

// ── Voice TTS ──

export function voiceStatus() { return client.get('/voice/status') }
/** GET /voice/speakers — 获取引擎发音人列表 */
export function getSpeakers(engine = 'mimo-tts') {
  return client.get('/voice/speakers', { params: { engine } })
}
export function voiceSynthesize(text: string, engine = '') {
  const form = new FormData()
  form.append('text', text)
  if (engine) form.append('engine', engine)
  return client.post('/voice/synthesize', form, { headers: { 'Content-Type': 'multipart/form-data' }, responseType: 'blob' })
}

// ── Plugins ──

export function plugins() { return client.get('/plugins') }
export function togglePlugin(name: string, enabled: boolean) { return client.post(`/plugins/${name}/toggle`, null, { params: { enabled } }) }

// ── 火爬虫人设增强 ──

export function enrichCharacter(characterId: string, name: string, maxDocs = 3) {
  return client.post(`/characters/${characterId}/enrich`, { name, max_docs: maxDocs })
}

// ── File Upload ──

export function uploadFile(file: File) {
  const form = new FormData()
  form.append('file', file)
  return client.post('/files/upload', form, { headers: { 'Content-Type': 'multipart/form-data' } })
}


