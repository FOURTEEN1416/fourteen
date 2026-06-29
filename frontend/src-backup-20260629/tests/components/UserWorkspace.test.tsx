import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import UserWorkspace from '../../pages/UserWorkspace'

// ── hoisted mock fns ──
const { mockUseUnifiedCharacters, mockNavigate } = vi.hoisted(() => ({
  mockUseUnifiedCharacters: vi.fn(),
  mockNavigate: vi.fn(),
}))

vi.mock('../../hooks/useQueries', () => ({
  useUnifiedCharacters: () => mockUseUnifiedCharacters(),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

// ── fixture ──

const FAKE_CHARS = [
  {
    id: 'c1',
    name: '小雅',
    description: '温柔',
    personality: { warmth: 0.8 },
    is_active: true,
    user_id: 'u123',
    version: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
    core_anchors: [],
    speaking_style: { gentle: 0.7 },
  },
  {
    id: 'c2',
    name: '小雪',
    description: '傲娇',
    personality: { cool: 0.9 },
    is_active: false,
    user_id: 'u123',
    version: 1,
    created_at: '2026-02-01T00:00:00Z',
    updated_at: '2026-05-01T00:00:00Z',
    core_anchors: [],
    speaking_style: { tsundere: 0.8 },
  },
]

// ── helper ──

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/users/:userId" element={<UserWorkspace />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('UserWorkspace', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // ── Test 1: Empty state ──

  it('shows empty state when no characters', () => {
    mockUseUnifiedCharacters.mockReturnValue({
      data: { characters: [], total: 0 },
      isLoading: false,
    })
    renderAt('/users/u123')

    // "暂无角色" appears in both stats row and empty state heading — use getAllByText
    const emptyTexts = screen.getAllByText('暂无角色')
    expect(emptyTexts.length).toBeGreaterThanOrEqual(1)

    expect(screen.getByText('创建你的第一个 AI 角色开始对话')).toBeDefined()
  })

  // ── Test 2: Character cards with names ──

  it('renders character cards with names', () => {
    mockUseUnifiedCharacters.mockReturnValue({
      data: { characters: FAKE_CHARS, total: 2 },
      isLoading: false,
    })
    renderAt('/users/u123')

    // Character names appear in avatar + name element — use getAllByText
    const xiaoya = screen.getAllByText('小雅')
    expect(xiaoya.length).toBeGreaterThanOrEqual(1)
    const xiaoxue = screen.getAllByText('小雪')
    expect(xiaoxue.length).toBeGreaterThanOrEqual(1)

    // Descriptions should be visible
    expect(screen.getByText('温柔')).toBeDefined()
    expect(screen.getByText('傲娇')).toBeDefined()
  })

  // ── Test 3: Click 创建角色 navigates ──

  it('click 创建角色 button navigates to roles/create', () => {
    mockUseUnifiedCharacters.mockReturnValue({
      data: { characters: FAKE_CHARS, total: 2 },
      isLoading: false,
    })
    renderAt('/users/u123')

    // Get the header "创建角色" button (the unique one when characters exist)
    fireEvent.click(screen.getByText('创建角色'))

    expect(mockNavigate).toHaveBeenCalledWith('/users/u123/roles/create')
  })

  // ── Test 4: Click character card navigates to settings ──

  it('click character card navigates to character settings', () => {
    mockUseUnifiedCharacters.mockReturnValue({
      data: { characters: FAKE_CHARS, total: 2 },
      isLoading: false,
    })
    renderAt('/users/u123')

    // "小雅" appears in avatar + name — click any occurrence
    fireEvent.click(screen.getAllByText('小雅')[0])

    expect(mockNavigate).toHaveBeenCalledWith('/users/u123/roles/c1/settings')
  })

  // ── Test 5: Loading state ──

  it('shows loading state while fetching', () => {
    mockUseUnifiedCharacters.mockReturnValue({
      data: undefined,
      isLoading: true,
    })
    renderAt('/users/u123')

    // Page heading is still visible
    expect(screen.getByText('用户工作区')).toBeDefined()

    // Stat values show dash when loading (角色总数, 活跃角色, 创建时间)
    const dashes = screen.getAllByText('-')
    expect(dashes.length).toBeGreaterThanOrEqual(2)
  })

  // ── Test 6: Multiple characters in grid ──

  it('multiple characters display in grid', () => {
    mockUseUnifiedCharacters.mockReturnValue({
      data: { characters: FAKE_CHARS, total: 2 },
      isLoading: false,
    })
    renderAt('/users/u123')

    // Both character names appear (in avatar + name elements)
    expect(screen.getAllByText('小雅').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('小雪').length).toBeGreaterThanOrEqual(1)

    // Stats show correct total
    expect(screen.getByText('2')).toBeDefined() // 角色总数
  })
})
