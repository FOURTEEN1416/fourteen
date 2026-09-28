/**
 * 角色模板面 API — /api/character-templates（W12 阶段1 端点的消费层）
 *
 * 后端真源：`api/routers/character_template_routes.py`。语义要点：
 * - 只读面：仅「无主 + 过策展」的卡摘要，不含 persona 正文；总开关关闭返回空清单。
 * - 克隆 ≠ 认领：产出**新 id + 归属调用者**的独立副本，模板文件零字节改动。
 * - 端点只认 Bearer 主体（无主体 401）；不存在/非无主/未过策展/开关关统一 404。
 */
import client from './client'

export interface CharacterTemplate {
  id: string
  name: string
  description: string
  tags: string[]
}

export interface TemplateListResponse {
  templates: CharacterTemplate[]
  total: number
}

export interface TemplateCloneReceipt {
  id: string
  name: string
  status: string
}

/** GET /api/character-templates */
export function listTemplates(): Promise<TemplateListResponse> {
  return client.get('/character-templates').then((r) => r.data as TemplateListResponse)
}

/** POST /api/character-templates/{id}/clone — 201 {id,name,status:"created"} */
export function cloneTemplate(templateId: string): Promise<TemplateCloneReceipt> {
  return client
    .post(`/character-templates/${encodeURIComponent(templateId)}/clone`)
    .then((r) => r.data as TemplateCloneReceipt)
}

/**
 * 克隆成功判据 = 业务回执：后端明确 `status:"created"` 且带回**新副本 id**。
 * 只看 HTTP 2xx 会把「回执里没有 id」的畸形成功当成克隆完成。
 *
 * 形参取 `Partial`：这是**外部响应边界**上的守卫，必须能接收缺字段的畸形回执。
 */
export function isCloneAccepted(
  receipt: Partial<TemplateCloneReceipt> | undefined | null,
): boolean {
  if (!receipt || typeof receipt !== 'object') return false
  return receipt.status === 'created' && typeof receipt.id === 'string' && receipt.id.length > 0
}
