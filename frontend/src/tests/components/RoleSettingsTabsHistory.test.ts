import { describe, it, expect } from 'vitest'
import {
  normalizeProactiveHistory,
  localDayKey,
  formatHistoryTime,
} from '../../components/admin/RoleSettingsTabs'

// ── NEW-7（2026-10-29）：主动消息历史契约归一化 ──
// 生产端点 /api/proactive/history 主径回 {kind, created_at(epoch 秒)}，
// 旧前端读 {type, at} → 「今日主动」恒 0 + Invalid Date。钉死边界归一化契约。

describe('normalizeProactiveHistory', () => {
  it('主径（agent_plane 账本）kind/created_at 归一化为 type/at', () => {
    const out = normalizeProactiveHistory([
      {
        id: 1,
        kind: 'proactive',
        channel: 'wechat',
        session_key: '4:wxid|林挽夏',
        message: '早上好呀',
        status: 'sent',
        created_at: 1769587200.123, // epoch 秒
        fail_reason: null,
      },
    ])
    expect(out).toHaveLength(1)
    expect(out[0].type).toBe('proactive')
    expect(out[0].message).toBe('早上好呀')
    const d = new Date(out[0].at)
    expect(Number.isNaN(d.getTime())).toBe(false)
    expect(d.getTime()).toBe(1769587200123) // 秒 → 毫秒
  })

  it('降级径（ASE 引擎内存）type/at 原样透传', () => {
    const out = normalizeProactiveHistory([
      { type: 'care', message: '晚安', at: '2026-10-28T12:00:00+00:00' },
    ])
    expect(out[0].type).toBe('care')
    expect(out[0].at).toBe('2026-10-28T12:00:00+00:00')
  })

  it('非法行（非对象/缺字段/畸形时间）安全跳过或落空，不抛异常', () => {
    const out = normalizeProactiveHistory([
      null,
      'garbage',
      { kind: 'x' }, // 无时间 → at=''
      { kind: 'y', created_at: 'not-a-number' },
      { kind: 'z', created_at: 0 }, // 0 视为无时间
    ])
    expect(out).toHaveLength(3) // null/'garbage' 整行跳过，其余落空 at
    expect(out.every(h => h.at === '' || !Number.isNaN(new Date(h.at).getTime()))).toBe(true)
    expect(out.map(h => h.type)).toEqual(['x', 'y', 'z'])
  })

  it('非数组输入返回空数组', () => {
    expect(normalizeProactiveHistory(undefined)).toEqual([])
    expect(normalizeProactiveHistory(null)).toEqual([])
    expect(normalizeProactiveHistory({})).toEqual([])
  })
})

describe('localDayKey（「今日」本地日界）', () => {
  it('按本地墙钟取日，不是 UTC 日', () => {
    // 真实本地构造：任何时区都成立（本地构造 → 本地读取）
    const d = new Date(2026, 9, 29, 0, 30, 0)
    expect(localDayKey(d)).toBe('2026-10-29')

    // 时区无关的跨日对照：用一个「本地字段与 UTC 字段不同」的替身 Date，
    // 钉死实现读的是 getFullYear/getMonth/getDate（本地），不是 toISOString（UTC）。
    // 旧断言用真实 Date + 硬编码东八区换算，在 CI runner（UTC）下必然失败
    // （`new Date(2026,9,29,0,30).toISOString()` 在 UTC 下就是 29 日）——
    // 2026-10-31 CI 首次暴露（v1.60 批 2 新增后本地 22 commit 未推送，从未上过 CI）。
    const crossDay = {
      getFullYear: () => 2026,
      getMonth: () => 9, // 10 月（0-based）
      getDate: () => 29, // 本地 29 日
      toISOString: () => '2026-10-28T16:30:00.000Z', // 同一时刻的 UTC 是 28 日
    } as unknown as Date
    expect(localDayKey(crossDay)).toBe('2026-10-29')
    expect(crossDay.toISOString().slice(0, 10)).toBe('2026-10-28')
    // 若实现退回 toISOString 口径，上面第一条会得 '2026-10-28' 而失败
    expect(localDayKey(crossDay)).not.toBe(crossDay.toISOString().slice(0, 10))
  })

  it('月/日两位补零', () => {
    expect(localDayKey(new Date(2026, 0, 5))).toBe('2026-01-05')
  })
})

describe('formatHistoryTime（Invalid Date 守卫）', () => {
  it('空串与畸形时间返回空串而非 "Invalid Date"', () => {
    expect(formatHistoryTime('', 'time')).toBe('')
    expect(formatHistoryTime('not-a-date', 'time')).toBe('')
    expect(formatHistoryTime('not-a-date', 'datetime')).toBe('')
  })

  it('合法 ISO 正常格式化（zh-CN 本地时区）', () => {
    const out = formatHistoryTime('2026-10-28T12:00:00', 'time')
    expect(out).not.toBe('')
    expect(out).toMatch(/^\d{2}:\d{2}/)
  })
})
