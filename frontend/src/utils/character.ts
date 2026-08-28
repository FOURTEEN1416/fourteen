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


/**
 * 角色卡描述摘要化（SP-5 P0，2026-08-28）
 *
 * 卡库实况（53 张实扫）：7 张 description 为 `[姓名:x][年龄:x]…` 方括号字段堆叠（写给 LLM 的
 * 结构化 prompt）、10 张以「你是…」开头（最长 1.2 万字）、28 张为空——这些是 prompt 载体不是
 * 产品文案，直接进 UI 是塑料感第一根因。本函数把任意形态收敛为 ≤60 字的第三人称展示摘要。
 */
export function deriveCardSummary(desc: string | undefined, name: string, anchors: string[] = []): string {
  const raw = (desc ?? '').trim()
  if (!raw) {
    // 空描述：用锚点拼最小可读句，避免空洞卡
    if (anchors.length > 0) return `${name}：${anchors.slice(0, 3).join(' · ')}`
    return '暂无描述，可在角色设置中补充'
  }

  // ── 形态 A：[字段:值] 方括号 prompt ──
  if (/^\s*\[/.test(raw)) {
    const fields = new Map<string, string>()
    for (const m of raw.matchAll(/\[([^\]\[:]{1,6})[:：]\s*([^\]]{1,120})\]/g)) {
      fields.set(m[1].trim(), m[2].trim())
    }
    if (fields.size > 0) {
      const pick = (keys: string[]): string => {
        for (const k of keys) {
          const v = fields.get(k)
          if (v) return v
        }
        return ''
      }
      const who = pick(['姓名', '名字']) || name
      const parts = [who]
      const age = pick(['年龄', '岁'])
      if (age) parts.push(`${age.replace(/岁$/, '')}岁`)
      const job = pick(['职业'])
      if (job) parts.push(job)
      const identity = pick(['身份', '设定', '背景'])
      if (identity) parts.push(identity)
      const looks = pick(['外貌', '外观'])
      if (looks) parts.push(looks)
      const summary = parts.join('，')
      return summary.length > 60 ? `${summary.slice(0, 58)}…` : summary
    }
  }

  // ── 形态 B：「你是X，…」第二人称设定文 ──
  let text = raw
  const youMatch = text.match(/^你是([\s\S]{2,})$/)
  if (youMatch) {
    text = youMatch[1]
    // 把首段「X，9岁女性，…」重挂为第三人称开头
    text = text.replace(/^([^，。；\n]{1,20})[，,]\s*/, '$1，')
  }
  // 取第一个完整句（到第一个句号/换行），剥残留方括号标记
  const firstSentence = text.split(/[。；;\n]/)[0] || text
  const cleaned = firstSentence.replace(/\[[^\]]*\]/g, '').replace(/\s+/g, ' ').trim()
  const summary = cleaned || raw
  return summary.length > 60 ? `${summary.slice(0, 58)}…` : summary
}

/**
 * 锚点标签语义配色（SP-5 P0，2026-08-28）
 *
 * 替代原 `i % 3` 轮转随机配色。规则：关系/情感类=暖黄，身份/设定类=海盐蓝，风格/行为类=薄荷青。
 */
const ANCHOR_RELATION = /恋|爱|妻|夫|男友|女友|兄妹|姐妹|哥哥|姐姐|妹妹|弟弟|青梅|竹马|暧昧|暗恋|告白|亲情|家人|挚友|朋友|闺蜜|主人|仆/
const ANCHOR_SETTING = /世界|王国|帝国|王朝|修仙|魔法|学院|学校|公司|组织|种族|精灵|公主|王子|女王|皇帝|穿越|异世界|现代|古代|未来|星际|赛博/
const ANCHOR_STYLE = /傲娇|温柔|病娇|腹黑|元气|高冷|天然|呆|毒舌|粘人|活泼|安静|可靠|可靠|勇敢|胆小|吃货|睡|玩|幽默|成熟|孩子气|反差/

export type AnchorTone = 'yellow' | 'blue' | 'mint'

export function anchorTone(tag: string): AnchorTone {
  if (ANCHOR_RELATION.test(tag)) return 'yellow'
  if (ANCHOR_SETTING.test(tag)) return 'blue'
  if (ANCHOR_STYLE.test(tag)) return 'mint'
  // 兜底：按长度奇偶稳定分配（同 tag 恒同色）
  return tag.length % 2 === 0 ? 'blue' : 'mint'
}
