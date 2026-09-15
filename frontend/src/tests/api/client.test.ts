/**
 * api/client.ts — 错误响应归一化回归测试
 *
 * 事故（2026-09-15 复赛演示环境）：
 *   FastAPI 请求校验失败（422）时 detail 是对象数组
 *     [{ type, loc, msg, input, ctx }, ...]
 *   页面级 catch 直接取 err.response.data.detail 塞进 setState / addToast，
 *   React 渲染对象 child 抛「Minified React error #31（object with keys
 *   {type, loc, msg, input, ctx}）」，整页白屏。
 * 修复：响应拦截器第 0 步把 detail 统一压平为字符串（normalizeDetail）。
 */
import { describe, it, expect, afterEach, beforeEach } from 'vitest'
import type { AxiosError, InternalAxiosRequestConfig } from 'axios'
import client, { normalizeDetail } from '../../api/client'
import { useErrorStore } from '../../store/errorStore'

/** 线上实测抓取的真实 422 响应体（POST /api/auth/login 空 body） */
const REAL_422_LOGIN = {
  detail: [
    { type: 'missing', loc: ['body', 'login'], msg: 'Field required', input: {} },
    { type: 'missing', loc: ['body', 'password'], msg: 'Field required', input: {} },
  ],
}

/** 带 ctx 的形态 —— 与报错信息中的 keys {type, loc, msg, input, ctx} 一致 */
const REAL_422_WITH_CTX = {
  detail: [
    {
      type: 'string_too_short',
      loc: ['body', 'password'],
      msg: 'String should have at least 6 characters',
      input: '1',
      ctx: { min_length: 6 },
    },
  ],
}

describe('normalizeDetail — 纯函数', () => {
  it('字符串原样返回', () => {
    expect(normalizeDetail('Invalid login credentials')).toBe('Invalid login credentials')
  })

  it('把 422 对象数组压平成 "loc: msg" 串', () => {
    expect(normalizeDetail(REAL_422_LOGIN.detail)).toBe(
      'body.login: Field required; body.password: Field required',
    )
  })

  it('带 ctx 的 issue 输出纯字符串，不泄漏对象', () => {
    const out = normalizeDetail(REAL_422_WITH_CTX.detail)
    expect(typeof out).toBe('string')
    expect(out).toBe('body.password: String should have at least 6 characters')
  })

  it('混合数组（字符串 + 无 loc 对象 + 空串）不产生空项', () => {
    expect(normalizeDetail(['直接错误', { msg: '无 loc 的消息' }, ''])).toBe(
      '直接错误; 无 loc 的消息',
    )
  })

  it('非数组对象兜底为 JSON 字符串', () => {
    expect(normalizeDetail({ foo: 'bar' })).toBe('{"foo":"bar"}')
  })

  it('null / undefined / 数字 → 空串', () => {
    expect(normalizeDetail(null)).toBe('')
    expect(normalizeDetail(undefined)).toBe('')
    expect(normalizeDetail(422)).toBe('')
  })
})

describe('响应拦截器 — 422 detail 归一化（防 React #31 白屏）', () => {
  let originalAdapter: unknown

  beforeEach(() => {
    originalAdapter = client.defaults.adapter
    useErrorStore.setState({ toasts: [], lastError: null })
  })

  afterEach(() => {
    client.defaults.adapter = originalAdapter as never
  })

  function stub422(body: unknown) {
    client.defaults.adapter = (async (config: InternalAxiosRequestConfig) => {
      throw Object.assign(new Error('Request failed with status code 422'), {
        isAxiosError: true,
        code: 'ERR_BAD_REQUEST',
        config,
        response: {
          data: structuredClone(body),
          status: 422,
          statusText: 'Unprocessable Entity',
          headers: {},
          config,
        },
        toJSON: () => ({}),
      })
    }) as never
  }

  it('reject 出来的 detail 已是字符串，页面 setState 不再拿到对象', async () => {
    stub422(REAL_422_LOGIN)
    const error = (await client.post('/auth/login', {}).catch((e) => e)) as AxiosError<{
      detail: unknown
    }>
    expect(typeof error.response?.data?.detail).toBe('string')
    expect(error.response?.data?.detail).toBe(
      'body.login: Field required; body.password: Field required',
    )
  })

  it('toast 的 message 也是字符串（toast 渲染对象同样会白屏）', async () => {
    stub422(REAL_422_WITH_CTX)
    await client.post('/auth/login', { password: '1' }).catch(() => undefined)
    const { toasts } = useErrorStore.getState()
    expect(toasts.length).toBeGreaterThan(0)
    for (const t of toasts) {
      expect(typeof t.message).toBe('string')
      expect(t.message).not.toContain('[object Object]')
    }
    expect(toasts.some((t) => t.message.includes('body.password'))).toBe(true)
  })
})
