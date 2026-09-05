import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// FastAPI 會以靜態檔案托管 dist/，掛載路徑不固定，因此 base 用相對路徑。
export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // SSE 必須逐塊送達，關閉任何緩衝／壓縮，並拉長逾時（摘要可能跑數十秒）。
        ws: false,
        timeout: 0,
        proxyTimeout: 0,
        headers: {
          'Accept-Encoding': 'identity',
        },
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            // 明確要求下游不要緩衝（對齊後端的 X-Accel-Buffering: no）
            proxyRes.headers['cache-control'] = 'no-cache, no-transform'
            delete proxyRes.headers['content-encoding']
          })
        },
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
  },
})
