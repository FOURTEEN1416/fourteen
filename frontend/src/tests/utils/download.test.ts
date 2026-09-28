import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { datedFilename, downloadJson } from '../../utils/download'

// ════════════════════════════════════════════════════════════════
//  W17：自服务面导出必须**落成文件**且文件名带日期。
//  契约来源：GET /api/auth/account/export、/account/export/chats
//  本工具是页面唯一的下载出口（不在页面里重复 createObjectURL 三遍）。
// ════════════════════════════════════════════════════════════════

describe('datedFilename', () => {
  it('拼出 "前缀-YYYY-MM-DD.ext"（本地日期，非 UTC 切片）', () => {
    const local = new Date(2026, 8, 28, 9, 30, 0) // 2026-09-28 本地
    expect(datedFilename('account-export', 'json', local)).toBe('account-export-2026-09-28.json')
  })

  it('月/日单位数补零', () => {
    const local = new Date(2026, 0, 5, 0, 0, 0)
    expect(datedFilename('chats-export', 'json', local)).toBe('chats-export-2026-01-05.json')
  })
})

describe('downloadJson', () => {
  let clicked: string[]
  let blobs: Blob[]
  let revoked: string[]

  beforeEach(() => {
    clicked = []
    blobs = []
    revoked = []
    // jsdom 不实现 URL.createObjectURL/revokeObjectURL（属性也不存在）→ defineProperty 注入
    Object.defineProperty(URL, 'createObjectURL', {
      value: (blob: Blob) => { blobs.push(blob); return 'blob:mock-url' },
      writable: true, configurable: true,
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      value: (url: string) => { revoked.push(url) },
      writable: true, configurable: true,
    })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this.download)
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    delete (URL as unknown as Record<string, unknown>).createObjectURL
    delete (URL as unknown as Record<string, unknown>).revokeObjectURL
  })

  it('触发一次同名下载，并在下载后释放 objectURL', () => {
    downloadJson('account-export-2026-09-28.json', { user_id: 7, categories: { chats: 3 } })

    expect(clicked).toEqual(['account-export-2026-09-28.json'])
    expect(revoked).toEqual(['blob:mock-url'])
  })

  it('内容按 JSON 序列化（UTF-8，中文不转义、可被再次解析）', async () => {
    const payload = { session_keys: ['7:peer'], 中文: '值' }
    downloadJson('chats-export-2026-09-28.json', payload)

    expect(blobs).toHaveLength(1)
    expect(blobs[0].type).toContain('application/json')
    const text = await blobs[0].text()
    expect(JSON.parse(text)).toEqual(payload)
    expect(text).toContain('值')
  })
})
