/**
 * Sentry 前端错误监控
 *
 * 通过 Vite 环境变量配置：
 *   VITE_SENTRY_DSN=https://your-dsn@sentry.io/123456
 *   VITE_SENTRY_ENVIRONMENT=production      （可选，默认 production）
 *   VITE_SENTRY_TRACES_SAMPLE_RATE=0.1      （可选，默认 0.1）
 *
 * 仅在 VITE_SENTRY_DSN 有值时启用。
 */

import * as Sentry from '@sentry/react'

export function initFrontendSentry(): void {
  const dsn = import.meta.env.VITE_SENTRY_DSN
  if (!dsn) return

  Sentry.init({
    dsn,
    environment: import.meta.env.VITE_SENTRY_ENVIRONMENT || 'production',
    tracesSampleRate: Number(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE) || 0.1,
    integrations: [
      Sentry.browserTracingIntegration(),
      Sentry.replayIntegration({
        maskAllText: true,
        blockAllMedia: true,
      }),
    ],
    // Session Replay 采样率：生产用 0.1（10%），降低性能开销
    replaysSessionSampleRate: 0.1,
    replaysOnErrorSampleRate: 1.0,
  })
}
