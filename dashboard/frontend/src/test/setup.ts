import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// RTL 不會自動卸載。這些元件在 window 上掛 keydown 監聽（設定覆蓋層的 Esc、
// 全域快捷鍵），沒卸載乾淨的話上一則測試的監聽器會在下一則被觸發。
afterEach(cleanup)
