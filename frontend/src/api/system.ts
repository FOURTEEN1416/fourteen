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
export function proactiveState() { return client.get('/proactive/state') }
export function proactiveHistory(limit = 50) { return client.get('/proactive/history', { params: { limit } }) }
export function updateProactiveConfig(cfg: { threshold?: number; max_daily?: number; min_interval_minutes?: number; cooldown_after_reply_minutes?: number }) {
  return client.post('/proactive/config', cfg)
}

// ── Logs ──

export function logs(params?: { limit?: number; level?: string; search?: string }) {
  return client.get('/logs', { params })
}

// ── Channels / WeChat ──

export function channels() { return client.get('/channels') }
export function wechatStatus() { return client.get('/channels/wechat/status') }
export function wechatReconnect() { return client.post('/channels/wechat/reconnect') }
export function wechatConnect() { return client.post('/channels/wechat/connect') }
export function wechatDisconnect() { return client.post('/channels/wechat/disconnect') }
export function wechatConnectionStatus() { return client.get('/channels/wechat/connection-status') }
export function wechatQrCode() { return client.get('/wechat/qrcode') }

// ── Psychology Profile ──

export function psychProfile() { return client.get('/psych/profile') }
export function psychSnapshots(limit = 20) { return client.get('/psych/snapshots', { params: { limit } }) }
export function psychReset() { return client.delete('/psych/profile') }
export function psychMentalHealth() { return client.get('/psych/mental-health') }
export function psychLiwc() { return client.get('/psych/liwc') }

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


