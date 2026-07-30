/**
 * LLM 供应商管理 API — /api/llm-providers/*
 *
 * 普通用户：读取启用的供应商清单 + 申请教程
 * admin：管理供应商（启用/禁用/添加/编辑/删除）
 */
import client from './client'

// ════════════════════════════════════════════════════
//  类型定义
// ════════════════════════════════════════════════════

/** 供应商申请教程 */
export interface ProviderGuide {
  apply_url: string
  free_quota: string
  steps: string[]
  tips: string[]
  warnings: string[]
}

/** 供应商清单项（GET /api/llm-providers 返回） */
export interface ProviderOption {
  key: string
  name: string
  description: string
  sort_order: number
  guide: ProviderGuide
  /** 特殊选项（auto/custom） */
  is_special: boolean
  /** 预设供应商（不可删除） */
  is_preset: boolean
  // 真实供应商才有以下字段（特殊选项没有）
  model?: string
  api_base?: string
  api_key?: string  // 脱敏后为 '****'
  auth_mode?: 'bearer' | 'oauth'
  max_tokens?: number
  temperature?: number
  stream_enabled?: boolean
  enabled?: boolean
  extra_payload?: Record<string, unknown>
}

/** GET /api/llm-providers 响应 */
export interface ListProvidersResponse {
  providers: ProviderOption[]
  default_provider: string
}

/** GET /api/llm-providers/all 响应（admin） */
export interface ListAllProvidersResponse extends ListProvidersResponse {
  fallback_chain: string[]
}

/** PUT /api/llm-providers/{key} 请求体（admin 编辑） */
export interface ProviderUpdateRequest {
  name: string
  model: string
  api_base: string
  api_key: string  // 空或 '****' 表示保留原值
  auth_mode: 'bearer' | 'oauth'
  max_tokens: number
  temperature: number
  stream_enabled: boolean
  description: string
  enabled: boolean
  sort_order: number
  guide: ProviderGuide
  extra_payload?: Record<string, unknown> | null
}

/** POST /api/llm-providers 请求体（admin 创建） */
export interface ProviderCreateRequest extends ProviderUpdateRequest {
  key: string  // 小写字母+数字+下划线
}

// ════════════════════════════════════════════════════
//  API 函数
// ════════════════════════════════════════════════════

/** GET /api/llm-providers — 普通用户：返回启用的供应商 + 特殊选项 */
export function listProviders(): Promise<ListProvidersResponse> {
  return client.get('/llm-providers').then(r => r.data as ListProvidersResponse)
}

/** GET /api/llm-providers/all — admin：返回所有供应商（含禁用的） */
export function listAllProviders(): Promise<ListAllProvidersResponse> {
  return client.get('/llm-providers/all').then(r => r.data as ListAllProvidersResponse)
}

/** POST /api/llm-providers — admin：添加新供应商 */
export function createProvider(data: ProviderCreateRequest): Promise<{ detail: string; provider: ProviderOption }> {
  return client.post('/llm-providers', data).then(r => r.data)
}

/** PUT /api/llm-providers/{key} — admin：更新供应商配置/教程 */
export function updateProvider(key: string, data: ProviderUpdateRequest): Promise<{ detail: string; provider: ProviderOption }> {
  return client.put(`/llm-providers/${key}`, data).then(r => r.data)
}

/** PUT /api/llm-providers/{key}/toggle — admin：启用/禁用供应商 */
export function toggleProvider(key: string, enabled: boolean): Promise<{ detail: string; enabled: boolean }> {
  return client.put(`/llm-providers/${key}/toggle`, { enabled }).then(r => r.data)
}

/** DELETE /api/llm-providers/{key} — admin：删除自定义供应商 */
export function deleteProvider(key: string): Promise<{ detail: string }> {
  return client.delete(`/llm-providers/${key}`).then(r => r.data)
}
