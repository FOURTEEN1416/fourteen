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
// NEW-13①（2026-10-29）：BYOK 连通性测试——错 Key 当场报错，不延迟到聊天才爆
export function testUserLlmConfig(cfg: Record<string, unknown>) {
  return client.post('/user/llm-config/test', { config: cfg }).then(r => r.data as { ok: boolean; latency_ms?: number; preview?: string; error?: string })
}

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

// ── 用户自助主动消息（D21：消息 tab 对全体用户开放） ──
// 为什么不复用 /proactive/config：那是 admin 门 + `orch._ase` 503 门，且参数作用于
// **全部会话**。普通用户只能改自己的节奏，故走 user 端点；作用域键由服务端从 token
// 派生（客户端自报归属一律忽略）。
//
// 🔴 回执形状以**交付树后端实现为准**（api/routers/training_routes.py::_proactive_config_payload）：
//   { status, effective: {enabled, paused, quiet_hours_start, quiet_hours_end,
//                          threshold, max_daily_messages, min_interval_minutes,
//                          cooldown_after_reply_minutes},
//     global_default: {同 8 键}, overridden_keys[], scope:'user'|'global',
//     user_key_present: bool }
//   · `effective` 是「用户覆盖 > 全局 > 默认」三级合并后的**平铺**生效值（含 paused）；
//     前端直接渲染，绝不做二次合并、也不自算「值等于全局值即未覆盖」。
//   · `global_default` 是继承基线（未覆盖字段的旁注），`overridden_keys` 是徽章真源
//     （对齐 utils/humanize_config.py::has_user_override 的「覆盖行存在与否」语义）。
//   · 暂停态在 GET 回执里就是 `effective.paused`；仅 POST /user/proactive/pause 额外回顶层 `paused`。
// 契约快照：.zcode/workflow-drafts/proactive-user-config-contract.md

/** 用户可自助覆盖的键（与 utils/proactive_config.py::FIELDS 同名同边界；不含 LLM/骰子闸） */
export interface UserProactiveEffective {
  enabled: boolean
  paused: boolean
  quiet_hours_start: number
  quiet_hours_end: number
  threshold: number
  max_daily_messages: number
  min_interval_minutes: number
  cooldown_after_reply_minutes: number
}

/** 可写子集（partial merge：只传本次改动的键） */
export interface UserProactiveConfigWrite {
  enabled?: boolean
  paused?: boolean
  quiet_hours_start?: number
  quiet_hours_end?: number
  threshold?: number
  max_daily_messages?: number
  min_interval_minutes?: number
  cooldown_after_reply_minutes?: number
}

export interface UserProactiveConfigResponse {
  status?: string
  /** 三级合并后的生效值（平铺；含 paused / enabled） */
  effective?: Partial<UserProactiveEffective>
  /** 继承基线：全局默认值（未覆盖字段的来源） */
  global_default?: Partial<UserProactiveEffective>
  /** 本人覆盖表中显式存在的键（徽章与逐字段标记真源） */
  overridden_keys?: string[]
  /** `user` = 存在本人覆盖行；`global` = 继承全局 */
  scope?: 'user' | 'global'
  /** 服务端是否解析出本人会话键（false = 未绑定微信，只有全局视图） */
  user_key_present?: boolean
  user_key?: string
  /** 仅 POST /user/proactive/pause 携带 */
  paused?: boolean
}

export function userProactiveGetConfig() { return client.get('/user/proactive-config') }
/** 部分覆盖：body 只含本次改动的键（未传键=继承，不写默认值） */
export function updateUserProactiveConfig(cfg: UserProactiveConfigWrite) {
  return client.post('/user/proactive-config', cfg)
}
/** 清除本人全部覆盖，恢复继承全局（幂等） */
export function clearUserProactiveConfig() { return client.delete('/user/proactive-config') }
/** 个人暂停态（只改暂停，不动 config） */
export function userProactivePause(paused: boolean) {
  return client.post('/user/proactive/pause', { paused })
}

// ── 用户自助回复模式（2026-10-21 R7） ──
// 为什么不复用 /proactive/config：那是 admin 门 + `orch._ase` 503 门；
// 普通用户既无 admin 角色，也不该被「主动引擎是否初始化」耦合。
// 后端从 JWT 派生 user_key（绝不接受客户端传 user_key）。
export function getMyReplyMode() { return client.get('/user/reply-mode') }

// NEW-34（2026-10-29）：用户自助拟人节奏（复用 user_humanize 按用户作用域机制；
// 作用域键服务端从 token 派生，未绑定微信 400）
export function getMyHumanize() { return client.get('/user/humanize') }
export function saveMyHumanize(cfg: Record<string, unknown>) { return client.post('/user/humanize', cfg) }
export function clearMyHumanize() { return client.delete('/user/humanize') }
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


