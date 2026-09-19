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
          // rolldown 原生 advancedChunks：manualChunks 函数 shim 在 rolldown-vite 下
          // 会把 react 核心模块错误并入 motion chunk（实测 sourcemap 证实），入口因此被迫预加载 framer-motion
          advancedChunks: {
            groups: [
              { name: 'vendor', test: /node_modules[\\/](react|react-dom|react-router|react-router-dom|scheduler)[\\/]/ },
              { name: 'ui', test: /node_modules[\\/]lucide-react/ },
              { name: 'state', test: /node_modules[\\/]zustand/ },
              { name: 'motion', test: /node_modules[\\/](framer-motion|motion-dom|motion-utils)/ },
              { name: 'query', test: /node_modules[\\/]@tanstack[\\/]/ },
              { name: 'sentry', test: /node_modules[\\/]@sentry[\\/]/ },
              { name: 'virtual', test: /node_modules[\\/](react-window|react-virtualized-auto-sizer)/ },
            ],
          },
        },
      },
    },
  }
})
