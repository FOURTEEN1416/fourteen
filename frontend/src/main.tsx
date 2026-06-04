import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App'

// ══════════════════════════════════════════════
// Sentry 错误监控（仅生产环境自动启用）
// ══════════════════════════════════════════════
if (import.meta.env.PROD && import.meta.env.VITE_SENTRY_DSN) {
  import('./sentry').then(({ initFrontendSentry }) => {
    initFrontendSentry()
  }).catch(() => {
    // Sentry 不可用不影响用户
  })
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
