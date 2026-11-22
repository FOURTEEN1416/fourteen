import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import IntroPage from '../../pages/IntroPage'

function renderComponent() {
  return render(
    <MemoryRouter>
      <IntroPage />
    </MemoryRouter>,
  )
}

describe('IntroPage', () => {
  it('renders the positioning headline and brand', () => {
    renderComponent()
    expect(screen.getByText('唯一的你')).toBeDefined()
    // 2026-09-18 首屏改造：定位文案由「微信扫码即用的多用户 LLM 情感陪伴系统」
    // 改为去术语版「装进微信里的长期 AI 陪伴伙伴」（面向非技术访客/评委）
    expect(screen.getByText('装进微信里的长期 AI 陪伴伙伴')).toBeDefined()
  })

  it('renders the four capability cards', () => {
    renderComponent()
    expect(screen.getByText('微信陪伴')).toBeDefined()
    expect(screen.getByText('长期记忆')).toBeDefined()
    expect(screen.getByText('主动搭话')).toBeDefined()
    expect(screen.getByText('语音克隆')).toBeDefined()
  })

  it('links registration to /login with open-registration copy (D20-①)', () => {
    renderComponent()
    const link = screen.getByRole('link', { name: /立即注册/ })
    expect(link.getAttribute('href')).toBe('/login')
    // D13 开放注册主链路：不得再把「邀请码制」说成注册前提
    expect(screen.queryByText(/注册采用邀请码制/)).toBeNull()
    // 2026-11-22：指南卡也补了「无需邀请码」对齐——用 getAllByText 断言两处都在
    expect(screen.getAllByText(/无需邀请码/).length).toBeGreaterThanOrEqual(1)
  })

  it('shows the MIT open-source badge with GitHub link', () => {
    renderComponent()
    expect(screen.getByText('完全免费开源 · MIT 许可证')).toBeDefined()
    const gh = screen.getByRole('link', { name: /GitHub 仓库/ })
    expect(gh.getAttribute('href')).toBe('https://github.com/FOURTEEN1416/fourteen')
  })
})
