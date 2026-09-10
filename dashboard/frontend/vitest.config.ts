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
          /**
           * 元件測試的逾時放寬到 15 秒（node project 維持預設的 5 秒）。
           *
           * `userEvent` 的每一次點擊／打字都走真實計時器並等 act 收斂，一則
           * 測試裡點五個分頁就是好幾輪。平常整包跑完約 3 秒，但 2026-09-10
           * 在 load average 268 的機器上，同一份測試花了 183 秒——**七到九項
           * 因為撞到 5 秒逾時而變紅，而且每次紅的不是同一組**。
           *
           * 那種紅燈最糟的地方是它看起來像功能壞了：訊息是「找不到某個元素」，
           * 不是「逾時」。放寬之後同一份測試在同樣的負載下全綠。
           *
           * 這不是把慢的測試藏起來——真的邏輯錯誤在 5 秒或 15 秒都一樣會紅，
           * 差別只在「機器忙的時候要不要給假訊號」。
           */
          testTimeout: 15_000,
        },
      },
    ],
  },
})
