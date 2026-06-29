import { describe, it, expect } from 'vitest'
import { render, screen } from '../utils/test-utils'
import AnimatedPage from '../../components/shared/AnimatedPage'

describe('AnimatedPage', () => {
  it('renders children correctly', () => {
    render(
      <AnimatedPage>
        <h1>Hello World</h1>
      </AnimatedPage>,
    )
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Hello World')
  })
})
