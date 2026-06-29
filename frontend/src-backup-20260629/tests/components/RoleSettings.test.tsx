import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import RoleSettings from '../../pages/RoleSettings'

// ════════════════════════════════════════════════════════════════
//  vi.hoisted() — 所有 mock fn 和常量必须在 vi.mock 工厂之前定义
// ════════════════════════════════════════════════════════════════

const { mockUseUnifiedCharacter, FAKE_CHARACTER } = vi.hoisted(() => {
  const FAKE_CHARACTER = {
    id: 'c1',
    name: '小雅',
    description: '温柔可爱的AI',
    personality: { warmth: 0.8, playfulness: 0.5 },
    speaking_style: { gentle: 0.7 },
    is_active: true,
    user_id: 'u1',
    version: 1,
    created_at: '2026-01-01',
    updated_at: '2026-06-01',
    core_anchors: ['善良', '温柔'],
    message: {
      proactive: true,
      dailyLimit: 20,
      minInterval: 15,
      cooldown: 30,
      urgency: 0.7,
    },
    stats: {
      messages: 128,
      memories: 45,
      avgResponse: '1.2s',
    },
    rag: {
      vectorDocs: 256,
      keywordIndex: 1024,
      hitRate: 87,
    },
    knowledgeDocs: ['角色设定.md', '对话风格.txt'],
    voice_config: null,
    catchphrases: ['好呀~', '没问题呢'],
  }

  return {
    mockUseUnifiedCharacter: vi.fn(),
    FAKE_CHARACTER,
  }
})

// ════════════════════════════════════════════════════════════════
//  Module mocks
// ════════════════════════════════════════════════════════════════

vi.mock('../../hooks/useQueries', () => ({
  useUnifiedCharacter: (...args: unknown[]) => mockUseUnifiedCharacter(...args),
}))

// ════════════════════════════════════════════════════════════════
//  Render helpers
// ════════════════════════════════════════════════════════════════

function renderComponent(initialEntries = ['/users/u1/roles/c1/settings']) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes>
        <Route path="/users/:userId/roles/:roleId/settings" element={<RoleSettings />} />
        <Route path="/users/:userId/roles/:roleId/settings/:tab" element={<RoleSettings />} />
      </Routes>
    </MemoryRouter>,
  )
}

// ════════════════════════════════════════════════════════════════
//  Test Suite
// ════════════════════════════════════════════════════════════════

describe('RoleSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // ────────────────────────────────────────────────
  //  1. 加载中状态
  // ────────────────────────────────────────────────
  it('shows loading state while character loads', () => {
    mockUseUnifiedCharacter.mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    })

    renderComponent()

    expect(screen.getByText('加载角色中...')).toBeDefined()
  })

  // ────────────────────────────────────────────────
  //  2. 角色不存在（error 或 null）
  // ────────────────────────────────────────────────
  it('shows "角色不存在" when character not found', () => {
    mockUseUnifiedCharacter.mockReturnValue({
      data: null,
      isLoading: false,
      error: new Error('not found'),
    })

    renderComponent()

    expect(screen.getByText('角色不存在或加载失败')).toBeDefined()
  })

  // ────────────────────────────────────────────────
  //  3. 渲染 6 个 tab
  // ────────────────────────────────────────────────
  it('renders 6 tabs: 基础/语音/消息/数据/表情包/时间线', () => {
    mockUseUnifiedCharacter.mockReturnValue({
      data: FAKE_CHARACTER,
      isLoading: false,
      error: null,
    })

    renderComponent()

    // 角色名称
    expect(screen.getByText('小雅')).toBeDefined()

    // 6 个 tab 按钮
    expect(screen.getByText('基础')).toBeDefined()
    expect(screen.getByText('语音')).toBeDefined()
    expect(screen.getByText('消息')).toBeDefined()
    expect(screen.getByText('数据')).toBeDefined()
    expect(screen.getByText('表情包')).toBeDefined()
    expect(screen.getByText('时间线')).toBeDefined()
  })

  // ────────────────────────────────────────────────
  //  4. 点击 tab 切换活动内容
  // ────────────────────────────────────────────────
  it('click tab switches active tab content', () => {
    mockUseUnifiedCharacter.mockReturnValue({
      data: FAKE_CHARACTER,
      isLoading: false,
      error: null,
    })

    renderComponent()

    // 默认「基础」tab：应该有角色名称输入
    expect(screen.getByDisplayValue('小雅')).toBeDefined()

    // 点击「消息」tab
    fireEvent.click(screen.getByText('消息'))
    // 消息 tab 显示「主动对话」相关
    expect(screen.getByText('允许角色主动发起对话')).toBeDefined()

    // 点击「数据」tab
    fireEvent.click(screen.getByText('数据'))
    // 数据 tab 显示数据概览
    expect(screen.getByText('数据概览')).toBeDefined()

    // 点击「表情包」tab
    fireEvent.click(screen.getByText('表情包'))
    // 表情包 tab 显示常用表情
    expect(screen.getByText('常用表情')).toBeDefined()
  })

  // ────────────────────────────────────────────────
  //  5. 默认 tab 为「基础」
  // ────────────────────────────────────────────────
  it('defaults to "basic" tab', () => {
    mockUseUnifiedCharacter.mockReturnValue({
      data: FAKE_CHARACTER,
      isLoading: false,
      error: null,
    })

    renderComponent()

    // 默认 tab 显示基础设置——角色名称输入框
    expect(screen.getByDisplayValue('小雅')).toBeDefined()
    // 性格特质区域可见
    expect(screen.getByText('性格特质')).toBeDefined()
  })
})
