import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useMemoryFacts } from '../../hooks/useQueries'

// ── NEW-5④（2026-10-29）：user_facts 表正文的列名是 fact，hook 在边界归一化为 content ──
// 旧实现原样透传 → StatusCenter「最近沉淀」读 content 恒空。

const { mockMemoryFacts } = vi.hoisted(() => ({ mockMemoryFacts: vi.fn() }))

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>()
  return {
    ...actual,
    api: { ...actual.api, memoryFacts: (...args: unknown[]) => mockMemoryFacts(...args) },
  }
})

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

describe('useMemoryFacts 边界归一化', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('后端 fact 列映射为 content（消费面契约不变）', async () => {
    mockMemoryFacts.mockResolvedValue({
      data: {
        facts: [
          { fact: '喜欢喝咖啡', category: 'preference', confidence: 0.9, user_key: '4:wxid' },
          { content: '旧形状行（向量库行）', confidence: 0.5 },
        ],
      },
    })
    const { result } = renderHook(() => useMemoryFacts(), { wrapper: makeWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    const rows = result.current.data ?? []
    expect(rows[0].content).toBe('喜欢喝咖啡')
    expect(rows[1].content).toBe('旧形状行（向量库行）')
    // 原始列不丢（归一化是补齐不是替换）
    expect((rows[0] as unknown as Record<string, unknown>).fact).toBe('喜欢喝咖啡')
  })

  it('空 facts 与缺 fact 键的行不炸', async () => {
    mockMemoryFacts.mockResolvedValue({ data: { facts: [{ category: 'x' }] } })
    const { result } = renderHook(() => useMemoryFacts('preference'), { wrapper: makeWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.[0]?.content).toBe('')
  })
})
