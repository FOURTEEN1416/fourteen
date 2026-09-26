/**
 * 心理筛查口径诚实化契约（W8 缺陷 G · 前端侧）。
 *
 * 后端 mental_health 产的是「关键词命中」的 0-1 相对信号（每个维度命中词数/4 封顶 1，
 * total_score 为各维度均值），既不是 PHQ-9 也不是 GAD-7 —— 但旧页面把它标成
 * 「抑郁指征（PHQ-9 口径）… / 27」「焦虑指征（GAD-7 口径）… / 21」。分母是临床量表的
 * 总分上限，读数却永远不可能超过 1.0，用户会把它当量表分数读。
 *
 * 本测试钉：① 页面不再出现量表名与量表分母；② 按后端自报口径显示（/ 1.0 + 命中维度 +
 * 证据强度 + 命中词）；③ 后端 caveat 原样透出，后端未回传时用同义兜底。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '../utils/test-utils'
import { api } from '../../api/client'
import PsychProfilePage from '../../pages/PsychProfilePage'

const ACTIVE_CHAR = { id: 'char-001', name: '小雅', description: '', is_active: true }

// 与后端 MentalHealthScreener('我最近很焦虑…也很害怕…什么胃口都没有…很累') 实跑输出逐字段一致
const MH = {
  timestamp: '2026-09-26T20:55:34.612463+00:00',
  depression: {
    sleep: 0.0, interest: 0.0, guilt: 0.0, energy: 0.25, concentration: 0.0,
    appetite: 0.0, psychomotor: 0.0, suicidal: 0.0,
    total_score: 0.031, peak_score: 0.25, level: 'mild', matched: ['累'],
    scale: '0-1', max_score: 1.0, method: 'keyword_hit',
    items_hit: 1, items_total: 8, evidence: 1,
    formula: '维度分=min(1, 命中词数/4); total_score=各维度均值; evidence=max(最强维度命中词数, 命中维度数); level 按 evidence 分档',
  },
  anxiety: {
    nervousness: 0.5, uncontrollable_worry: 0.0, worry_too_much: 0.0,
    trouble_relaxing: 0.0, restlessness: 0.0, irritability: 0.0, fear_awful: 0.0,
    total_score: 0.071, peak_score: 0.5, level: 'mild', matched: ['焦虑', '害怕'],
    scale: '0-1', max_score: 1.0, method: 'keyword_hit',
    items_hit: 1, items_total: 7, evidence: 2,
    formula: '维度分=min(1, 命中词数/4); total_score=各维度均值; evidence=max(最强维度命中词数, 命中维度数); level 按 evidence 分档',
  },
  trauma_signals: 0.0,
  self_harm_risk: 0.0,
  overall_risk: 'low',
  method: 'keyword_hit',
  caveat: '关键词命中的情绪信号强度分档，不是临床量表得分，不能作为诊断或筛查结论使用',
}

const PROFILE = {
  user_id: 'char-001:user4:peerA',
  scope: 'char-001:user4:peerA',
  scope_count: 1,
  status: 'learning',
  stability: 0.4,
  snapshots: 7,
  ocean: { openness: 0.5, conscientiousness: 0.5, extraversion: 0.5, agreeableness: 0.5, neuroticism: 0.5 },
  pad: { pleasure: 0.1, arousal: 0.1, dominance: 0.1 },
  style: { formality: 0.4, expressiveness: 0.6, humor: 0.3, directness: 0.5, sentiment: 0.5 },
  mental_health: MH,
  first_seen: '2026-09-01T00:00:00',
  last_updated: '2026-09-26T00:00:00',
}

let spies: Array<() => void> = []

function spy(name: string, value: unknown) {
  const target = api as unknown as Record<string, (...args: unknown[]) => Promise<unknown>>
  const s = vi.spyOn(target, name).mockResolvedValue(value)
  spies.push(() => s.mockRestore())
  return s
}

beforeEach(() => {
  spies = []
  spy('listCharacters', { characters: [ACTIVE_CHAR], total: 1 })
  spy('psychSnapshots', { data: { snapshots: [] } })
})

afterEach(() => {
  spies.forEach(fn => fn())
})

async function renderWithMh(mh: unknown) {
  spy('psychProfile', { data: { ...PROFILE, mental_health: mh } })
  render(<PsychProfilePage />)
  return screen.findByText(/char-001:user4:peerA/)
}

describe('PsychProfilePage 情绪信号口径', () => {
  it('不再出现临床量表名与量表分母', async () => {
    await renderWithMh(MH)
    const html = document.body.textContent ?? ''
    for (const banned of ['PHQ', 'GAD', 'DSM', '/ 27', '/ 21']) {
      expect(html).not.toContain(banned)
    }
  })

  it('按后端自报口径显示：0-1 分值 + 命中维度 + 证据强度 + 命中词', async () => {
    await renderWithMh(MH)
    expect(await screen.findByText('低落信号（关键词命中）')).toBeTruthy()
    expect(screen.getByText('0.03')).toBeTruthy()
    expect(screen.getByText(/命中维度 1\/8/)).toBeTruthy()
    expect(screen.getByText(/证据强度 1/)).toBeTruthy()
    expect(screen.getByText('命中词：累')).toBeTruthy()

    expect(screen.getByText('焦虑信号（关键词命中）')).toBeTruthy()
    expect(screen.getByText('0.07')).toBeTruthy()
    expect(screen.getByText(/命中维度 1\/7/)).toBeTruthy()
    expect(screen.getByText('命中词：焦虑、害怕')).toBeTruthy()
  })

  it('透出后端 caveat', async () => {
    await renderWithMh(MH)
    expect(await screen.findByText(/不是临床量表得分/)).toBeTruthy()
  })

  it('后端未回传 caveat 时仍自带同等免责，不静默留白', async () => {
    const bare = { ...MH, caveat: undefined }
    await renderWithMh(bare)
    expect(await screen.findByText(/不是临床量表得分/)).toBeTruthy()
  })
})
