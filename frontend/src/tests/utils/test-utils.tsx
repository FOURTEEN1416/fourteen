import { type ReactElement } from 'react'
import { render, type RenderOptions } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'

/**
 * 带 Providers 的 render 封装
 * 所有测试组件都走这个入口，确保路由 / store 上下文一致
 */
function AllTheProviders({ children }: { children: React.ReactNode }) {
  return (
    <BrowserRouter>
      {children}
    </BrowserRouter>
  )
}

function customRender(
  ui: ReactElement,
  options?: Omit<RenderOptions, 'wrapper'>,
) {
  return render(ui, { wrapper: AllTheProviders, ...options })
}

// 重新导出 testing-library 的方法
export * from '@testing-library/react'
export { customRender as render }
