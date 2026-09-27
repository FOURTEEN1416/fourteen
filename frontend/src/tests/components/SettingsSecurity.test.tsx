import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import SettingsSecurity from '../../pages/SettingsSecurity'
import { useAuthStore } from '../../store/authStore'
import { useErrorStore } from '../../store/errorStore'

// ════════════════════════════════════════════════════════════════
//  W11-D1 红测：安全面板必须消费真实后端契约
//  GET /api/safety/stats → {enabled,total_flagged,recent_flagged,by_category,recent}
//  GET /api/safety/log   → {"log":[{timestamp,category,direction,text_length,text_hash}]}
//  POST /api/safety/config → admin-only；{"status":"ok"} | {"status":"not_available"}
// ════════════════════════════════════════════════════════════════

const { mockSafetyStats, mockSafetyLog, mockSafetyConfig } = vi.hoisted(() => ({
  mockSafetyStats: vi.fn(),
  mockSafetyLog: vi.fn(),
  mockSafetyConfig: vi.fn(),
}))

vi.mock('../../api/system', () => ({
  safetyStats: (...args: unknown[]) => mockSafetyStats(...args),
  safetyLog: (...args: unknown[]) => mockSafetyLog(...args),
  safetyConfig: (...args: unknown[]) => mockSafetyConfig(...args),
}))

const REAL_STATS = {
  enabled: true,
  total_flagged: 7,
  recent_flagged: 2,
  by_category: { self_harm: 2, violence: 1 },
}

const REAL_LOG = {
  log: [
    { timestamp: 1758000000, category: 'self_harm', direction: 'input', text_length: 42, text_hash: 'deadbeef00112233' },
    { timestamp: 1757900000, category: 'violence', direction: 'output', text_length: 18, text_hash: 'cafebabe00112233' },
  ],
}

function makeUser(role: 'admin' | 'viewer') {
  useAuthStore.setState({
    user: {
      id: role === 'admin' ? 1 : 2,
      email: `${role}@test.local`,
      username: role,
      display_name: role,
      avatar_url: '',
      role,
      is_active: true,
      is_verified: true,
      created_at: '2026-01-01T00:00:00Z',
      last_login_at: null,
    },
    isAuthenticated: true,
  })
}

function toasts() {
  return useErrorStore.getState().toasts
}

describe('SettingsSecurity（安全面板真契约）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useErrorStore.setState({ toasts: [], lastError: null })
    mockSafetyStats.mockResolvedValue({ data: REAL_STATS })
    mockSafetyLog.mockResolvedValue({ data: REAL_LOG })
    mockSafetyConfig.mockResolvedValue({ data: { status: 'ok', enabled: false } })
  })

  it('普通用户：可见本人安全统计与安全事件（log 键），无 admin 开关', async () => {
    makeUser('viewer')
    render(<SettingsSecurity />)

    // 统计卡渲染真实字段值
    expect(await screen.findByText('累计拦截')).toBeDefined()
    expect(screen.getByText('7')).toBeDefined()
    expect(screen.getByText('近期事件')).toBeDefined()

    // 安全事件来自 {"log":[...]} —— 旧代码读 .logs 恒空
    expect(await screen.findByText(/自伤风险/)).toBeDefined()
    expect(screen.getByText(/暴力/)).toBeDefined()

    // admin-only 开关对普通用户隐藏（后端继续强制）
    expect(screen.queryByText('内容安全过滤器')).toBeNull()
  })

  it('空日志显示「暂无安全事件」，不显示假数据', async () => {
    makeUser('viewer')
    mockSafetyLog.mockResolvedValue({ data: { log: [] } })
    render(<SettingsSecurity />)

    expect(await screen.findByText('暂无安全事件')).toBeDefined()
    expect(screen.queryByText(/自伤风险/)).toBeNull()
  })

  it('事件条目按 direction 区分输入/输出且时间可读', async () => {
    makeUser('viewer')
    render(<SettingsSecurity />)

    expect(await screen.findByText('输入')).toBeDefined()
    expect(screen.getByText('输出')).toBeDefined()
  })

  it('admin：可见开关；业务成功（status=ok）才翻转状态', async () => {
    makeUser('admin')
    render(<SettingsSecurity />)

    const toggle = await screen.findByText('内容安全过滤器')
    expect(toggle).toBeDefined()

    fireEvent.click(toggle.parentElement!.querySelector('button')!)

    await waitFor(() => {
      expect(mockSafetyConfig).toHaveBeenCalledWith(false)
    })
    // status=ok → 状态翻转为 已暂停
    expect(await screen.findByText('已暂停')).toBeDefined()
  })

  it('admin：业务失败（status=not_available）不翻转、给 warning 提示——成功必须是业务成功', async () => {
    makeUser('admin')
    mockSafetyConfig.mockResolvedValue({ data: { status: 'not_available' } })
    render(<SettingsSecurity />)

    const toggle = await screen.findByText('内容安全过滤器')
    fireEvent.click(toggle.parentElement!.querySelector('button')!)

    await waitFor(() => {
      expect(mockSafetyConfig).toHaveBeenCalled()
    })
    // 🔴 状态保持「运行中」，绝不假成功
    await waitFor(() => {
      expect(screen.getByText('运行中')).toBeDefined()
    })
    expect(screen.queryByText('已暂停')).toBeNull()
    // warning toast 明示不可用
    await waitFor(() => {
      expect(toasts().some((t) => t.type === 'warning')).toBe(true)
    })
  })

  it('加载失败显示错误与重试入口，不显示 0 冒充数据', async () => {
    makeUser('viewer')
    mockSafetyStats.mockRejectedValue({ response: { status: 500 } })
    render(<SettingsSecurity />)

    expect(await screen.findByText('无法加载安全数据')).toBeDefined()
    expect(screen.getByText('重试')).toBeDefined()
    expect(screen.queryByText('累计拦截')).toBeNull()
  })
})
