import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules') && !id.includes('/src/components/')) {
            return undefined
          }
          if (id.includes('react-router-dom') || id.includes('@tanstack/react-query')) {
            return 'routing-and-state'
          }
          if (
            id.includes('react-markdown') ||
            id.includes('remark-gfm') ||
            id.includes('lucide-react')
          ) {
            return 'markdown-and-rendering'
          }
          if (
            id.includes('/node_modules/react/') ||
            id.includes('/node_modules/react-dom/')
          ) {
            return 'react-core'
          }
          if (id.includes('/src/components/teach/TeachTab.jsx')) {
            return 'route-teach'
          }
          if (id.includes('/src/components/settings/SettingsTab.jsx')) {
            return 'route-settings'
          }
          if (id.includes('/src/components/query/QueryTab.jsx')) {
            return 'route-query'
          }
          if (id.includes('/src/components/graph/GraphTab.jsx')) {
            return 'route-graph'
          }
          if (id.includes('/src/components/history/HistoryTab.jsx')) {
            return 'route-history'
          }
          return undefined
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'https://localhost:8443',
        changeOrigin: true,
        secure: false,
        headers: {
          origin: 'https://localhost:8443',
        },
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{js,jsx}'],
      exclude: [
        'src/assets/**',
        'src/styles/**',
        'src/test/**',
        'src/main.jsx',
      ],
      thresholds: {
        lines: 80,
        functions: 80,
        statements: 80,
        branches: 70,
      },
    },
  },
})
