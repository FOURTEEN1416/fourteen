/**
 * api/system.ts —— 退役端点死 API 面的静态守卫（2026-11-08 D-A/D-B）。
 *
 * 背景：用户裁决把「主动消息节奏」从账号级偏好改成「用户 × 角色」设置，
 * 后端 `GET/POST/DELETE /api/user/proactive-config` 与
 * `POST /api/user/proactive/pause` 四条自助路由已删除。若前端还留着对应的
 * client 函数/hook，调用点会打到 404 —— 前一版自报「全仓 grep 残留清零」时
 * 恰好漏了这一批（只有组件内 import 被删，client 层仍在）。
 *
 * 本文件钉住：这些名字**不存在**于 `api/system` 与其再导出面（`api/client`），
 * 任何一方把它们加回来都会红。
 */
import { describe, it, expect } from 'vitest'
import * as systemApi from '../../api/system'
import * as clientApi from '../../api/client'

/** 已退役的四个用户自助主动消息函数（连同 `/api/user/proactive*` 两条路由面） */
const RETIRED = [
  'userProactiveGetConfig',
  'updateUserProactiveConfig',
  'clearUserProactiveConfig',
  'userProactivePause',
] as const

/** 已退役的 hook（`frontend/src/hooks/useQueries.ts`） */
const RETIRED_HOOKS = ['useUserProactiveConfig'] as const

describe('api/system：退役的用户自助主动消息面已清零', () => {
  it.each(RETIRED)('system.ts 不再导出 %s', (name) => {
    expect((systemApi as Record<string, unknown>)[name]).toBeUndefined()
  })

  it.each(RETIRED)('client.ts 不再再导出 %s（含 api 命名空间对象）', (name) => {
    expect((clientApi as unknown as Record<string, unknown>)[name]).toBeUndefined()
    expect((clientApi.api as unknown as Record<string, unknown>)[name]).toBeUndefined()
  })
})

describe('useQueries：退役 hook 已删除', () => {
  it.each(RETIRED_HOOKS)('%s 不存在', async (name) => {
    const mod = await import('../../hooks/useQueries')
    expect((mod as unknown as Record<string, unknown>)[name]).toBeUndefined()
  })
})
