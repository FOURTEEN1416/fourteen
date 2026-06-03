import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },
  reporter: [
    ['list'],
    ['html', { outputFolder: '../playwright-report' }],
  ],
  use: {
    baseURL: process.env.BASE_URL || 'http://127.0.0.1:5199',
    headless: true,
    viewport: { width: 1280, height: 720 },
    actionTimeout: 15_000,
    screenshot: 'only-on-failure',
    trace: 'on-first-retry',
  },
  webServer: [
    {
      command: process.env.CI ? 'bun run preview --port 5199 --host 127.0.0.1' : 'bun run dev',
      url: 'http://127.0.0.1:5199',
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      cwd: '.',
      env: {
        VITE_API_BASE: 'http://localhost:8000',
        ...(process.env.CI ? {} : { VITE_DEV_PORT: '5199' }),
      },
    },
  ],
})

