import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { ShortcutHelp } from './ShortcutHelp'
import { SHORTCUT_HELP } from '@/lib/hotkeys'
import { useUiStore } from '@/store/ui'

/**
 * 快捷鍵說明面板（`?`，設計規格 §11.1）。
 *
 * 「表上寫的鍵真的解析得出動作」由 `lib/hotkeys.test.ts` 守著；這一份只驗
 * 渲染與關閉行為——特別是 **Esc 要 preventDefault**，因為設定中心那個掛在
 * window 上的 Esc 監聽器就是靠 `defaultPrevented` 判斷「內層已經處理掉了」。
 * 少了它，設定開著時按一次 Esc 會把說明與設定一起關掉（2026-09-10 由真
 * 瀏覽器 E2E 抓到同型的 bug）。
 */

beforeEach(() => {
  useUiStore.setState({ helpOpen: false })
})

describe('ShortcutHelp', () => {
  it('關著就完全不渲染', () => {
    const { container } = render(<ShortcutHelp />)
    expect(container).toBeEmptyDOMElement()
  })

  it('開著時逐條列出 §11.1 那張表', () => {
    useUiStore.setState({ helpOpen: true })
    render(<ShortcutHelp />)

    expect(screen.getByRole('dialog', { name: '鍵盤快捷鍵' })).toBeInTheDocument()
    for (const row of SHORTCUT_HELP) {
      expect(screen.getByText(row.label, { exact: false })).toBeInTheDocument()
    }
  })

  it('**每一列的按鍵都畫成 kbd**，不是一串純文字', () => {
    useUiStore.setState({ helpOpen: true })
    const { container } = render(<ShortcutHelp />)

    const expected = SHORTCUT_HELP.reduce((sum, row) => sum + row.keys.length, 0)
    expect(container.querySelectorAll('kbd')).toHaveLength(expected)
  })

  it('說出「清單類的鍵只在焦點在清單上時有效」', () => {
    // 那是決定這個鍵能不能用的資訊，不是補充說明，所以要在畫面上讀得到
    useUiStore.setState({ helpOpen: true })
    render(<ShortcutHelp />)
    expect(screen.getAllByText(/焦點在左欄清單上時/).length).toBe(
      SHORTCUT_HELP.filter((row) => row.scope === 'list').length,
    )
  })

  it('說明送出類動作為什麼沒有快捷鍵', () => {
    useUiStore.setState({ helpOpen: true })
    render(<ShortcutHelp />)
    expect(screen.getByText(/不可撤回的動作不該有肌肉記憶/)).toBeInTheDocument()
  })

  it('**Esc 關閉，而且會 preventDefault**（設定的 Esc 守衛靠它）', () => {
    useUiStore.setState({ helpOpen: true })
    render(<ShortcutHelp />)

    const event = new KeyboardEvent('keydown', {
      key: 'Escape',
      bubbles: true,
      cancelable: true,
    })
    window.dispatchEvent(event)

    expect(useUiStore.getState().helpOpen).toBe(false)
    expect(event.defaultPrevented).toBe(true)
  })

  it('（正對照）別的鍵不會關掉它，也不會被吃掉', () => {
    useUiStore.setState({ helpOpen: true })
    render(<ShortcutHelp />)

    const event = new KeyboardEvent('keydown', { key: 'a', bubbles: true, cancelable: true })
    window.dispatchEvent(event)

    expect(useUiStore.getState().helpOpen).toBe(true)
    expect(event.defaultPrevented).toBe(false)
  })

  it('關閉鈕有說得清楚的可及名稱', async () => {
    useUiStore.setState({ helpOpen: true })
    render(<ShortcutHelp />)

    await userEvent.click(screen.getByRole('button', { name: '關閉快捷鍵說明' }))
    expect(useUiStore.getState().helpOpen).toBe(false)
  })

  it('**渲染結果裡 [title] 選得到 0 個**（規格 §10.3）', () => {
    // 這個面板全是「哪個鍵做什麼」，正是最容易被寫成 tooltip 的內容。
    // 守門測試 tokens.test.ts 掃的是原始碼，這一條驗的是渲染後的 DOM
    useUiStore.setState({ helpOpen: true })
    const { container } = render(<ShortcutHelp />)
    expect(container.querySelectorAll('[title]')).toHaveLength(0)
  })
})
