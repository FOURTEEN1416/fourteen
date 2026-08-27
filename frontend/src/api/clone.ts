import client from './client'

export interface ClonePersonaPreview {
  persona: {
    name: string
    description?: string
    core_anchors?: string[]
    personality?: Record<string, number>
    speaking_style?: Record<string, number>
  }
  sample_count: number
  style_report: Record<string, unknown>
  preview?: Array<{ user: string; reply: string }>
}

/** POST /api/clone/upload — 上传本地提取的聊天数据，服务器分析生成人设预览 */
export function cloneUpload(target: string, file: File): Promise<ClonePersonaPreview> {
  const form = new FormData()
  form.append('file', file)
  return client.post('/clone/upload', form, {
    params: { target },
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data as ClonePersonaPreview)
}

/** GET /api/clone/contacts — 联系人列表 */
export function cloneContacts(keyword = '') {
  return client.get('/clone/contacts', { params: { keyword } })
}

/** GET /api/clone/datasets — 数据集列表 */
export function cloneDatasets() {
  return client.get('/clone/datasets')
}

/** GET /api/clone/datasets/{personId} — 数据集详情 */
export function cloneDatasetDetail(personId: string, params?: {
  page?: number; pageSize?: number; keyword?: string;
  dateFrom?: string; dateTo?: string; onlyUser?: boolean
}) {
  return client.get(`/clone/datasets/${personId}`, { params: {
    page: params?.page, page_size: params?.pageSize,
    keyword: params?.keyword, date_from: params?.dateFrom,
    date_to: params?.dateTo, only_user: params?.onlyUser,
  }})
}

/** DELETE /api/clone/datasets/{personId} — 删除数据集 */
export function cloneDeleteDataset(personId: string) {
  return client.delete(`/clone/datasets/${personId}`)
}

/** DELETE /api/clone/datasets/{personId}/conversation — 删除对话 */
export function cloneDeleteConversation(personId: string, index: number) {
  return client.delete(`/clone/datasets/${personId}/conversation`, { params: { index } })
}

/** POST /api/clone/datasets/{personId}/conversations/batch-delete — 批量删除 */
export function cloneBatchDeleteConversations(personId: string, indices: number[]) {
  return client.post(`/clone/datasets/${personId}/conversations/batch-delete`, null, { params: { indices: indices.join(',') } })
}

/** GET /api/clone/stats — 克隆统计 */
export function cloneStats() {
  return client.get('/clone/stats')
}
