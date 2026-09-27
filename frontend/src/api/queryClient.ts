import { QueryClient } from '@tanstack/react-query'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30 * 1000,
      gcTime: 5 * 60 * 1000,
      retry: 1,
      refetchOnWindowFocus: true,
    },
  },
})

/**
 * 账号级查询缓存清理（W11-D3）。
 *
 * 背景：私人查询（dashboard / emotion / persona / memory / psych / safety …）的
 * queryKey 不带账号维度（`useQueries.ts` 的 queryKeys 是静态数组），因此缓存是
 * **进程级共享**的。A 登出后若不清空，B 登录的瞬间会用 A 的旧缓存渲染首帧
 * （多用户隔离硬约束在客户端面的破口）。
 *
 * 契约：
 * - **退出登录**必须调用（无论登出 API 成功与否）。
 * - **账号切换**（登录/注册返回的 user.id 与当前不同）必须调用。
 * - **同一账号重复登录不调用**（避免无谓的缓存失效闪烁）。
 *
 * 实现取 `queryClient.clear()`：一次性清空 queryCache 与 mutationCache，
 * 幂等、空缓存上重复调用不抛错。
 */
export function clearAccountScopedCache(): void {
  queryClient.clear()
}
