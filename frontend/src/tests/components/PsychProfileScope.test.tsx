/**
 * 心理画像页 scope 同源契约（W8 缺陷 E · 前端侧）。
 *
 * 生成侧把画像写在 `{character_id}:{session_id}` 键上，而旧页面不带任何过滤条件
 * 就读后端默认的 `default` 桶 —— 标题写着「角色 X 对你的理解」，内容却是另一个
 * 键空间的空/陈旧数据；「重置」也只清那一个空桶却对用户承诺"清除所有"。
 *
 * 本测试钉三件事：① 取数带当前角色 id；② 页面说明显示的是哪一份、共几份；
 * ③ 清除按当前角色，且成功提示用后端回传的真实清除份数。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '../utils/test-utils'
import { api } from '../../api/client'
import PsychProfilePage from '../../pages/PsychProfilePage'

const ACTIVE_CHAR = { id: 'char-001', name: '小雅', description: '', is_active: true }

const PROFILE = {
  user_id: 'char-001:user4:peerA',
  scope: 'char-001:user4:peerA',
  scope_count: 2,
  status: 'learning',
  stability: 0.4,
  snapshots: 7,
  ocean: { openness: 0.5, conscientiousness: 0.5, extraversion: 0.5, agreeableness: 0.5, neuroticism: 0.5 },
  pad: { pleasure: 0.1, arousal: 0.1, dominance: 0.1 },
  style: { formality: 0.4, expressiveness: 0.6, humor: 0.3, directness: 0.5, sentiment: 0.5 },
  first_seen: '2026-09-01T00:00:00',
  last_updated: '2026-09-26T00:00:00',
}

const RESET_RESULT = { status: 'reset', cleared: 2, scopes: ['char-001:user4:peerA', 'char-001:user4:peerB'] }

let spies: Array<() => void> = []

type Call = (...args: unknown[]) => Promise<unknown>

function spy(name: string, value: unknown) {
  const target = api as unknown as Record<string, Call>
  const s = vi.spyOn(target, name).mockResolvedValue(value)
  spies.push(() => s.mockRestore())
  return s
}

beforeEach(() => {
  spies = []
  spy('listCharacters', { characters: [ACTIVE_CHAR], total: 1 })
  spy('psychSnapshots', { data: { snapshots: [] } })
  spy('psychReset', { data: RESET_RESULT })
})

afterEach(() => {
  spies.forEach(fn => fn())
})

describe('PsychProfilePage 画像 scope 同源', () => {
  it('读画像/快照时带上当前角色 id（与生成侧键空间同源）', async () => {
    const profile = spy('psychProfile', { data: PROFILE })
    const snapshots = spy('psychSnapshots', { data: { snapshots: [] } })
    render(<PsychProfilePage />)

    // 首帧还没有角色列表（characterId=undefined），角色到位后按 id 重新取数
    await waitFor(() => expect(profile).toHaveBeenCalledWith('char-001'))
    expect(snapshots).toHaveBeenCalledWith(10, 'char-001')
  })

  it('页面说明显示的是哪一份画像、该角色共几份', async () => {
    spy('psychProfile', { data: PROFILE })
    render(<PsychProfilePage />)

    expect(await screen.findByText(/char-001:user4:peerA/)).toBeTruthy()
    expect(screen.getByText(/共\s*2\s*份会话画像/)).toBeTruthy()
  })

  it('清除只针对当前角色，成功提示用后端回传的真实份数', async () => {
    spy('psychProfile', { data: PROFILE })
    const reset = spy('psychReset', { data: RESET_RESULT })
    render(<PsychProfilePage />)

    fireEvent.click(await screen.findByText('重置画像'))
    expect(await screen.findByText(/角色「小雅」名下已学习的 2 份/)).toBeTruthy()
    fireEvent.click(screen.getByText('确认重置'))

    await waitFor(() => expect(reset).toHaveBeenCalledWith('char-001'))
  })

  it('该角色尚无画像时如实说"样本还不够"，不显示别人的画像', async () => {
    spy('psychProfile', {
      data: { ...PROFILE, status: 'insufficient_data', scope_count: 0, snapshots: 0 },
    })
    render(<PsychProfilePage />)

    expect(await screen.findByText('对话样本还不够')).toBeTruthy()
    expect(screen.getByText(/「小雅」还没有从与你的对话中学到足够特征/)).toBeTruthy()
    expect(screen.queryByText(/char-001:user4:peerA/)).toBeNull()
  })
})
