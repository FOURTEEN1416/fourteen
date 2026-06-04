import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import NotFoundPage from '../../pages/NotFoundPage'

function renderComponent() {
  return render(
    <MemoryRouter>
      <NotFoundPage />
    </MemoryRouter>,
  )
}

describe('NotFoundPage', () => {
  it('renders the 404 heading and description', () => {
    renderComponent()
    expect(screen.getByText('页面未找到')).toBeDefined()
    expect(screen.getByText('你访问的页面不存在或已被移除')).toBeDefined()
  })

  it('has a link to home labeled 返回仪表盘', () => {
    renderComponent()
    const link = screen.getByRole('link', { name: '返回仪表盘' })
    expect(link).toBeDefined()
    expect(link.getAttribute('href')).toBe('/')
  })
})
