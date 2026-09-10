import path from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const alias = { '@': path.resolve(import.meta.dirname, './src') }

/**
 * 兩個 project（設計規格 §15.4）。
 *
 * 純函式測試（`*.test.ts`）維持 node 環境，執行條件與新增元件測試之前**完全一樣**
 * ——它們是這次改版唯一的既有安全網，不該因為引入 jsdom 而換一個跑法。
 * 元件測試（`*.test.tsx`）另開 jsdom project，只有它需要 DOM 與 React 的
 * JSX transform。
 */
export default defineConfig({
  resolve: { alias },
  test: {
    projects: [
      {
        resolve: { alias },
        test: {
          name: 'node',
          environment: 'node',
          include: ['src/**/*.test.ts'],
        },
      },
      {
        plugins: [react()],
        resolve: { alias },
        test: {
          name: 'jsdom',
          environment: 'jsdom',
          include: ['src/**/*.test.tsx'],
          setupFiles: ['./src/test/setup.ts'],
        },
      },
    ],
  },
})
