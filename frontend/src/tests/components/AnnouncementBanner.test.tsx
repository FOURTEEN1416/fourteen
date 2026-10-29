import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AnnouncementBanner from '../../components/shared/AnnouncementBanner'
import { useAuthStore } from '../../store/authStore'

// V2-5（2026-10-29）：公告横幅——未读逐条展示 + 「知道了」落库已读水位。

const { mockListActive, mockMarkRead } = vi.hoisted(() => ({
  mockListActive: vi.fn(),
  mockMarkRead: vi.fn(),
}))

vi.mock('../../api/announcements', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/announcements')>()),
  listActiveAnnouncements: (...args: unknown[]) => mockListActive(...args),
  markAnnouncementRead: (...args: unknown[]) => mockMarkRead(...args),
}))

const VIEWER = {
  id: 2, email: 'v@t', username: 'viewer', display_name: 'Viewer',
  role: 'viewer' as const, is_active: true, is_verified: true,
}

const FAKE_ANN = {
  id: 5,
  type: 'banner' as const,
  title: '欢迎使用',
  content_md: '先看使用说明，问题联系 QQ 2053769154',
  link: '/intro',
  link_label: '查看使用说明',
  dismissible: true,
  starts_at: null,
  ends_at: null,
  enabled: true,
  read: false,
}

function renderBanner() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AnnouncementBanner />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('AnnouncementBanner', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAuthStore.setState({ user: VIEWER } as never)
    mockMarkRead.mockResolvedValue({ status: 'ok' })
  })

  it('未登录不渲染', () => {
    useAuthStore.setState({ user: null } as never)
    mockListActive.mockResolvedValue({ announcements: [] })
    renderBanner()
    expect(screen.queryByTestId('announcement-banner')).toBeNull()
  })

  it('渲染未读公告（标题/正文/深链）', async () => {
    mockListActive.mockResolvedValue({ announcements: [FAKE_ANN] })
    renderBanner()
    expect(await screen.findByText('欢迎使用')).toBeDefined()
    expect(screen.getByText(/QQ 2053769154/)).toBeDefined()
    expect(screen.getByText('查看使用说明 →')).toBeDefined()
  })

  it('已读公告不再出现', async () => {
    mockListActive.mockResolvedValue({ announcements: [{ ...FAKE_ANN, read: true }] })
    renderBanner()
    await waitFor(() => expect(mockListActive).toHaveBeenCalled())
    expect(screen.queryByText('欢迎使用')).toBeNull()
  })

  it('「知道了」乐观收起并调用 markRead', async () => {
    mockListActive.mockResolvedValue({ announcements: [FAKE_ANN] })
    renderBanner()
    fireEvent.click(await screen.findByLabelText('关闭公告：欢迎使用'))
    expect(mockMarkRead).toHaveBeenCalledWith(5)
    await waitFor(() => expect(screen.queryByText('欢迎使用')).toBeNull())
  })
})
