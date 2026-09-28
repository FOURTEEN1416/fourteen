import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SettingsAccount from '../../pages/SettingsAccount'
import { useErrorStore } from '../../store/errorStore'
import { AGREEMENT_VERSION } from '../../constants/agreement'

// ════════════════════════════════════════════════════════════════
//  W17 红测：账号自服务面（/settings/account）
//  后端真源（只读核对）：
//    GET  /api/auth/consent/status   → {status, agreement_version}
//    POST /api/auth/consent/withdraw → {status}
//    POST /api/auth/account/delete   → {job_id,user_id,status,completed,steps}（无 deleted 宣称）
//    GET  /api/auth/account/export   → {user_id,session_keys,categories{…}}
//    GET  /api/auth/account/export/chats → {messages,next_before_id}
//  纪律：① 判定一律业务回执；② 注销回执不得被说成「已删除」；
//       ③ 导出必须落成文件且文件名带日期；④ 注销成功要清私有缓存回登录页。
// ════════════════════════════════════════════════════════════════

const {
  mockGetConsentStatus, mockWithdrawConsent, mockConsent,
  mockDeleteAccount, mockExportManifest, mockExportAllChats,
  mockDownloadJson, mockDatedFilename, mockLogout, mockNavigate,
} = vi.hoisted(() => ({
  mockGetConsentStatus: vi.fn(),
  mockWithdrawConsent: vi.fn(),
  mockConsent: vi.fn(),
  mockDeleteAccount: vi.fn(),
  mockExportManifest: vi.fn(),
  mockExportAllChats: vi.fn(),
  mockDownloadJson: vi.fn(),
  mockDatedFilename: vi.fn((prefix: string, ext: string) => `${prefix}-2026-09-28.${ext}`),
  mockLogout: vi.fn(async () => undefined),
  mockNavigate: vi.fn(),
}))

vi.mock('../../api/selfservice', () => ({
  getConsentStatus: (...a: unknown[]) => mockGetConsentStatus(...a),
  withdrawConsent: (...a: unknown[]) => mockWithdrawConsent(...a),
  deleteAccount: (...a: unknown[]) => mockDeleteAccount(...a),
  exportAccountManifest: (...a: unknown[]) => mockExportManifest(...a),
  exportAllChats: (...a: unknown[]) => mockExportAllChats(...a),
  isDeleteAccepted: (receipt: { status?: string } | undefined) =>
    !!receipt && ['queued', 'purging', 'completed'].includes(String(receipt.status)),
  DELETE_ACCEPTED_STATUSES: ['queued', 'purging', 'completed'],
}))

vi.mock('../../api/auth', () => ({
  consent: (...a: unknown[]) => mockConsent(...a),
}))

vi.mock('../../utils/download', () => ({
  downloadJson: (...a: unknown[]) => mockDownloadJson(...a),
  datedFilename: (...a: unknown[]) => mockDatedFilename(...(a as [string, string])),
}))

vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ logout: mockLogout }),
}))

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

const MANIFEST = {
  user_id: 7,
  session_keys: ['7:peerA'],
  categories: {
    chats: 12, facts: 4, reflections: 1, reminders: 2, profile: 3,
    diary: 5, consents: 2, wechat_bindings: 1, wechat_channels: 1,
  },
}

function mount() {
  return render(
    <MemoryRouter>
      <SettingsAccount />
    </MemoryRouter>,
  )
}

function toasts() {
  return useErrorStore.getState().toasts.map((t) => `${t.type}:${t.message}`)
}

beforeEach(() => {
  vi.clearAllMocks()
  useErrorStore.setState({ toasts: [], lastError: null })
  mockGetConsentStatus.mockResolvedValue({ status: 'granted', agreement_version: AGREEMENT_VERSION })
  mockExportManifest.mockResolvedValue(MANIFEST)
})

describe('同意状态展示与撤回', () => {
  it('展示后端真实状态与协议版本（不硬编码「已同意」）', async () => {
    mount()

    await waitFor(() => expect(mockGetConsentStatus).toHaveBeenCalled())
    expect(await screen.findByText(/已同意 v/)).toBeInTheDocument()
  })

  it('outdated 状态如实显示「需重新同意」，并给出重新同意入口', async () => {
    mockGetConsentStatus.mockResolvedValue({ status: 'outdated', agreement_version: AGREEMENT_VERSION })

    mount()

    await screen.findByText(/协议版本有更新/)
    expect(screen.getByRole('button', { name: '重新同意' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /撤回同意/ })).not.toBeInTheDocument()
  })

  it('撤回成功（回执 status=withdrawn）→ 状态更新为已撤回 + 成功回执，并暴露恢复入口', async () => {
    mockWithdrawConsent.mockResolvedValue({ status: 'withdrawn' })

    mount()
    fireEvent.click(await screen.findByRole('button', { name: /撤回同意/ }))

    await waitFor(() => expect(mockWithdrawConsent).toHaveBeenCalled())
    expect(await screen.findByText(/已撤回同意/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新同意' })).toBeInTheDocument()
    expect(toasts().some((t) => t.includes('已撤回'))).toBe(true)
  })

  it('撤回假成功守卫：回执状态不是 withdrawn 时绝不更新为已撤回，只给 warning', async () => {
    // 后端异常回执（例如仍返回 granted）：HTTP 成功 ≠ 业务成功
    mockWithdrawConsent.mockResolvedValue({ status: 'granted' })

    mount()
    fireEvent.click(await screen.findByRole('button', { name: /撤回同意/ }))

    await waitFor(() => expect(mockWithdrawConsent).toHaveBeenCalled())
    await waitFor(() => expect(toasts().length).toBeGreaterThan(0))
    expect(toasts().every((t) => !t.includes('success:已撤回'))).toBe(true)
    expect(screen.queryByRole('button', { name: '重新同意' })).not.toBeInTheDocument()
  })

  it('重新同意复用既有 POST /api/auth/consent（当前版本），成功后回读状态', async () => {
    mockGetConsentStatus
      .mockResolvedValueOnce({ status: 'withdrawn', agreement_version: AGREEMENT_VERSION })
      .mockResolvedValueOnce({ status: 'granted', agreement_version: AGREEMENT_VERSION })

    mount()
    fireEvent.click(await screen.findByRole('button', { name: '重新同意' }))

    await waitFor(() => expect(mockConsent).toHaveBeenCalledWith(AGREEMENT_VERSION))
    await waitFor(() => expect(mockGetConsentStatus).toHaveBeenCalledTimes(2))
  })
})

describe('数据导出', () => {
  it('展示清单真实类别计数，缺字段显示 0 不编造', async () => {
    mount()

    await screen.findByText('对话记录')
    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
  })

  it('导出我的数据 → 落 account-export 文件（文件名带日期）', async () => {
    mount()
    fireEvent.click(await screen.findByRole('button', { name: /导出我的数据/ }))

    await waitFor(() => expect(mockDownloadJson).toHaveBeenCalled())
    const [filename, payload] = mockDownloadJson.mock.calls[0] as [string, typeof MANIFEST]
    expect(filename).toBe('account-export-2026-09-28.json')
    expect(payload.categories.chats).toBe(12)
  })

  it('导出聊天记录 → 走分页聚合后落 chats-export 文件', async () => {
    mockExportAllChats.mockResolvedValue({
      user_id: 7, exported_at: 'x', sessions: { '7:peerA': [{ id: 1 }] }, total_messages: 1,
    })

    mount()
    fireEvent.click(await screen.findByRole('button', { name: /导出聊天记录/ }))

    await waitFor(() => expect(mockExportAllChats).toHaveBeenCalled())
    const [filename, payload] = mockDownloadJson.mock.calls[0] as [string, { total_messages: number }]
    expect(filename).toBe('chats-export-2026-09-28.json')
    expect(payload.total_messages).toBe(1)
  })

  it('清单加载失败：明确报错 + 重试入口，不拿 0 冒充数据', async () => {
    mockExportManifest.mockRejectedValue(new Error('boom'))

    mount()

    await screen.findByText(/无法加载账号数据/)
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
    expect(screen.queryByText('对话记录')).not.toBeInTheDocument()
  })
})

describe('注销账号', () => {
  it('输入不等于「注销」时按钮禁用（二次确认门禁）', async () => {
    mount()

    const input = await screen.findByTestId('delete-confirm-input')
    const button = screen.getByRole('button', { name: /注销账号/ })
    expect(button).toBeDisabled()

    fireEvent.change(input, { target: { value: '注销' } })
    await waitFor(() => expect(screen.getByRole('button', { name: /注销账号/ })).toBeEnabled())
  })

  it('受理回执（queued）→ 清私有缓存 + 回登录页；文案绝不宣称「已删除」', async () => {
    mockDeleteAccount.mockResolvedValue({
      job_id: 'acct-7', user_id: 7, status: 'queued', completed: false, steps: {},
    })

    mount()
    const input = await screen.findByTestId('delete-confirm-input')
    fireEvent.change(input, { target: { value: '注销' } })
    fireEvent.click(screen.getByRole('button', { name: /注销账号/ }))

    await waitFor(() => expect(mockDeleteAccount).toHaveBeenCalled())
    await waitFor(() => expect(mockLogout).toHaveBeenCalled())
    expect(mockNavigate).toHaveBeenCalledWith('/login')
    expect(toasts().join('|')).not.toContain('已删除')
    expect(toasts().some((t) => t.includes('已受理'))).toBe(true)
  })

  it('未受理（status=failed）→ 不登出、不跳转，给 error', async () => {
    mockDeleteAccount.mockResolvedValue({
      job_id: 'acct-7', user_id: 7, status: 'failed', completed: false, steps: {},
    })

    mount()
    const input = await screen.findByTestId('delete-confirm-input')
    fireEvent.change(input, { target: { value: '注销' } })
    fireEvent.click(screen.getByRole('button', { name: /注销账号/ }))

    await waitFor(() => expect(mockDeleteAccount).toHaveBeenCalled())
    await waitFor(() => expect(toasts().length).toBeGreaterThan(0))
    expect(mockLogout).not.toHaveBeenCalled()
    expect(mockNavigate).not.toHaveBeenCalledWith('/login')
  })
})
