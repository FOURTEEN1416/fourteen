/**
 * 角色名称清洗 — 与 backend utils.character_helpers.sanitize_character_name 保持语义一致
 * 用于前端兜底，防止后端漏清洗或本地预览数据带噪。
 */
export function sanitizeCharacterName(name: string): string {
  if (!name) return '未命名角色'

  let cleaned = name.trim().replace(/\.json$/i, '').trim()

  // 常见元数据前缀
  cleaned = cleaned.replace(/^[Pp]ersona_\s*/, '')
  cleaned = cleaned.replace(/^[（(]人设[)）]\s*/, '')

  // 括号内作者/定制/by 标记
  cleaned = cleaned.replace(/[（(][^）)]*(?:by|BY|定制|作者|著)[^）)]*[）)]/g, '')
  // 纯元数据括号块
  cleaned = cleaned.replace(/[（(]\s*\d+\s*[）)]/g, '')
  cleaned = cleaned.replace(
    /[（(]\s*(?:正常|学校|女友|旅游|定制|by|BY|作者|著)\s*[）)]/g,
    '',
  )
  // 描述性/签名性括号块（长度 >= 2 的任何括号内容）
  cleaned = cleaned.replace(/[（(][^）)]{2,}?[）)]/g, '')
  // 已知签名词
  cleaned = cleaned.replace(/听得见|银子著|银子|BY诗|by诗/gi, '')

  // 尾部作者署名
  cleaned = cleaned.replace(/[-_–—]\s*(?:作者|著|by|BY|银子|听得见|诗)\S*$/i, '')
  // 尾部时间戳
  cleaned = cleaned.replace(/[_-]\d{13,15}$/, '')

  // 首尾装饰标点
  cleaned = cleaned.replace(/^[\s：:·•\-–—_]+/, '')
  cleaned = cleaned.replace(/[\s：:·•\-–—_]+$/, '')

  // 多余空白
  cleaned = cleaned.replace(/[\s_–—]+/g, ' ').trim()

  return cleaned || name.trim().replace(/\.json$/i, '').trim() || '未命名角色'
}
