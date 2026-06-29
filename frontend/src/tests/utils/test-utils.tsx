/* eslint-disable react-refresh/only-export-components */
import { type ReactElement } from 'react'
import { render, type RenderOptions } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

/**
 * 带 Providers 的 render 封装
 * 所有测试组件都走这个入口，确保路由 / React Query / store 上下文一致
 */
function customRender(
  ui: ReactElement,
  options?: Omit<RenderOptions, 'wrapper'>,
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })

  function AllTheProviders({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          {children}
        </BrowserRouter>
      </QueryClientProvider>
    )
  }

  return render(ui, { wrapper: AllTheProviders, ...options })
}

// 重新导出 testing-library 的方法
export * from '@testing-library/react'
export { customRender as render }
