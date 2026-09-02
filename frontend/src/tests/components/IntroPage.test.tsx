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
    expect(screen.getByText('微信扫码即用的多用户 LLM 情感陪伴系统')).toBeDefined()
  })

  it('renders the four capability cards', () => {
    renderComponent()
    expect(screen.getByText('微信陪伴')).toBeDefined()
    expect(screen.getByText('长期记忆')).toBeDefined()
    expect(screen.getByText('主动搭话')).toBeDefined()
    expect(screen.getByText('语音克隆')).toBeDefined()
  })

  it('links invite registration to /login', () => {
    renderComponent()
    const link = screen.getByRole('link', { name: /使用邀请码注册/ })
    expect(link.getAttribute('href')).toBe('/login')
  })

  it('shows the MIT open-source badge with GitHub link', () => {
    renderComponent()
    expect(screen.getByText('完全免费开源 · MIT 许可证')).toBeDefined()
    const gh = screen.getByRole('link', { name: /GitHub 仓库/ })
    expect(gh.getAttribute('href')).toBe('https://github.com/FOURTEEN1416/fourteen')
  })
})
