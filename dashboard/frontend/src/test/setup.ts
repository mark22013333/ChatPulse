import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// jsdom 沒有佈局，所以沒有實作 scrollIntoView（呼叫會丟 TypeError）。
// 命令面板與虛擬清單都用它把高亮項捲進可視範圍。補一個 no-op：
// **捲動位置本來就不該在 jsdom 驗**（規格 §15.4），這裡只是讓元件跑得完。
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}

// RTL 不會自動卸載。這些元件在 window 上掛 keydown 監聽（設定覆蓋層的 Esc、
// 全域快捷鍵），沒卸載乾淨的話上一則測試的監聽器會在下一則被觸發。
afterEach(cleanup)
