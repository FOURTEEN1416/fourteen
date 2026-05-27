import client from './client'

// ── Health & Stats ──

export function health() { return client.get('/health') }
export function stats() { return client.get('/stats') }
export function dashboardStats() { return client.get('/stats/dashboard') }

// ── Config ──

export function config() { return client.get('/config') }
export function saveConfig(cfg: Record<string, unknown>) { return client.post('/config', { config: cfg }) }

// ── Persona ──

export function personaProfile() { return client.get('/persona/profile') }
export function personaEvolutionLog(limit = 50) { return client.get('/persona/evolution-log', { params: { limit } }) }

// ── Memory Facts ──

export function memoryFacts(category = '', limit = 50) {
  return client.get('/memory/facts', { params: { category, limit } })
}

// ── Tools & Proactive ──

export function tools() { return client.get('/tools') }
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
export function getSpeakers(engine = 'edge-tts') {
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

// ── File Upload ──

export function uploadFile(file: File) {
  const form = new FormData()
  form.append('file', file)
  return client.post('/files/upload', form, { headers: { 'Content-Type': 'multipart/form-data' } })
}

// ── Shisi Legacy — still-used old routes (no unified equivalent yet) ──

// Affinity
export function affinityGet(cid: string) { return client.get(`/shisi/affinity/${cid}`) }
export function affinityUpdate(cid: string, delta: number, reason: string) {
  return client.post(`/shisi/affinity/${cid}/update`, { delta, reason, source: 'web' })
}
export function affinityUnlocks(cid: string) { return client.get(`/shisi/affinity/${cid}/unlocks`) }
export function affinityDecay(cid: string) { return client.post(`/shisi/affinity/${cid}/decay`) }

// Emotion Stage
export function emotionStageGet(cid: string) { return client.get(`/shisi/emotion-stage/${cid}`) }
export function emotionStageList() { return client.get('/shisi/emotion-stage/stages') }
export function emotionStageEvaluate(cid: string, affinity: number) {
  return client.post(`/shisi/emotion-stage/${cid}/evaluate`, null, { params: { affinity } })
}

// Vital Signs
export function vitalSignsGet(cid: string) { return client.get(`/shisi/vital-signs/${cid}`) }

// Characters (shisi legacy — still needed for old CharacterState type)
export function shisiCharactersList() { return client.get('/shisi/characters') }

// Stats (shisi legacy)
export function shisiStats() { return client.get('/shisi/stats') }

// Stickers
export function stickerList(category?: string) { return client.get('/shisi/stickers', { params: { category } }) }
export function stickerDelete(id: string) { return client.delete(`/shisi/stickers/${id}`) }
export function stickerRecommend(emotionTags: string[], limit = 5) {
  return client.post('/shisi/stickers/recommend', { emotion_tags: emotionTags, limit })
}
export function stickerImportZip(formData: FormData) {
  return client.post('/shisi/stickers/import', formData, { headers: { 'Content-Type': 'multipart/form-data' } })
}
export function stickerUpload(formData: FormData) {
  return client.post('/shisi/stickers/import', formData, { headers: { 'Content-Type': 'multipart/form-data' } })
}
export function stickerBindToCharacter(characterId: string, stickerIds: string[], unlockThreshold = 0) {
  return client.put(`/shisi/stickers/characters/${characterId}/stickers`, { sticker_ids: stickerIds, unlock_threshold: unlockThreshold })
}

// Memory (shisi legacy)
export function shisiMemoryFavorites(cid: string) { return client.get('/shisi/memory/favorites', { params: { character_id: cid } }) }
export function shisiMemoryAddFavorite(cid: string, memoryId: string) {
  return client.post('/shisi/memory/favorite', { character_id: cid, memory_id: memoryId })
}
export function shisiMemoryRemoveFavorite(favId: string, cid?: string) {
  return client.delete(`/shisi/memory/favorite/${favId}`, { params: { character_id: cid } })
}
export function shisiMemoryForward(fromCid: string, toCid: string, memoryId: string, content?: string) {
  return client.post('/shisi/memory/forward', { from_character: fromCid, to_character: toCid, memory_id: memoryId, content })
}
export function shisiMemoryDelete(memoryId: string, cid?: string) {
  return client.delete(`/shisi/memory/${memoryId}`, { params: { character_id: cid, confirm: true } })
}

// Voice Training (shisi legacy)
export function voiceTrainingUpload(files: File[], modelName = 'default') {
  const fd = new FormData()
  files.forEach(f => fd.append('files', f))
  fd.append('model_name', modelName)
  return client.post('/shisi/voice/training/upload', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
}
export function voiceTrainingPreprocess(modelName: string) {
  return client.post('/shisi/voice/training/preprocess', { model_name: modelName })
}
export function voiceTrainingTrain(modelName: string, epochs = 8, batchSize = 4) {
  return client.post('/shisi/voice/training/train', { model_name: modelName, epochs, batch_size: batchSize })
}
export function voiceTrainingStatus() {
  return client.get('/shisi/voice/training/status')
}

// Persona (shisi legacy)
export function shisiPersonaGet(cid: string) { return client.get(`/shisi/persona/characters/${cid}`) }
export function shisiPersonaUpdate(cid: string, fields: Record<string, unknown>) {
  return client.put(`/shisi/persona/characters/${cid}`, { card: fields })
}
export function shisiPersonaPreview(cid: string) { return client.get(`/shisi/persona/characters/${cid}/preview`) }
