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
  /** 2026-11-08：只写**全局默认**（用户级模式走角色设置 my-settings） */
  reply_mode?: 'immersive' | 'novel'
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

// ── 用户自助主动消息（D21）已于 2026-11-08 退役 ──
// 用户裁决（D-A/D-B）：主动消息的节奏参数是「用户 × 角色」的体验参数，不是账号级
// 偏好。后端 `GET/POST/DELETE /api/user/proactive-config` 与
// `POST /api/user/proactive/pause` 四条自助路由随 per-user 覆盖层一并删除
// （DELETION_LOG 2026-11-08 条目），设置改由 my-settings 快照承载
// （`api/characterSettings.ts`）；admin 全局控制面仍走 `/api/proactive/*`。
// 因此本文件不再导出对应的 client 函数与回执类型（旧面留着就是打 404 的死代码）。

// ── 用户自助回复模式 / 拟人节奏（2026-10-21 R7 / NEW-34）已退役 ──
// 2026-11-08 D-A 用户裁决：这两项是「用户 × 角色」的体验参数，不是账号级偏好——
// 前端自助入口（账号页个人偏好区）与对应后端端点 GET/POST /api/user/reply-mode、
// GET/POST/DELETE /api/user/humanize 一并退役；前端改由「我 × 角色」设置面板的
// my-settings 快照承载（reply_mode 进快照，拟人节奏走既有人格/节奏字段）。

// ── Humanize / 话题状态机 管理端前端函数（2026-11-08 已退役） ──
// 唯一消费方 HumanizeSection / TopicStateSection / humanizeStore / topicStateStore
// 随 D-B 整机删除的运维页成为无挂载点死组件，经用户裁决「没有用就可以删除」一并
// 退役（DELETION_LOG 2026-11-08 条目）。后端 admin 端点 `/api/humanize/config`、
// `/api/topic-state/*` 按 D-B「只保留既有 API 与代码常量」保留，运维走 API/配置。

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


