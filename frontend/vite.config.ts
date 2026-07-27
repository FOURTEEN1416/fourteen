/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    test: {
      globals: true,
      environment: 'jsdom',
      setupFiles: ['./src/tests/setup.ts'],
      css: true,
      include: ['src/**/*.{test,spec}.{ts,tsx}'],
      exclude: ['node_modules', 'e2e', 'dist'],
    },
    server: {
      port: Number(env.VITE_DEV_PORT) || 5173,
      proxy: {
        '/api': {
          target: env.VITE_API_BASE || 'http://localhost:8000',
          changeOrigin: true,
        },
        '/ws': {
          target: env.VITE_WS_URL?.replace('ws://', 'http://') || 'ws://localhost:8765',
          ws: true,
        },
      },
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks(id: string) {
            if (id.includes('node_modules/react-dom') || id.includes('node_modules/react/') || id.includes('node_modules/react-router')) return 'vendor';
            if (id.includes('node_modules/lucide-react')) return 'ui';
            if (id.includes('node_modules/zustand')) return 'state';
            // F3: 分离 framer-motion，避免打进主 chunk（约 50-60kB gzip）
            if (id.includes('node_modules/framer-motion')) return 'motion';
            // F3: 分离 react-query，独立 chunk 并行下载
            if (id.includes('node_modules/@tanstack/react-query')) return 'query';
            // F3: 分离 Sentry（仅生产环境用，按需加载）
            if (id.includes('node_modules/@sentry')) return 'sentry';
            // F3: 分离 react-window（虚拟列表，仅部分页面用）
            if (id.includes('node_modules/react-window') || id.includes('node_modules/react-virtualized-auto-sizer')) return 'virtual';
          },
        },
      },
    },
  }
})
