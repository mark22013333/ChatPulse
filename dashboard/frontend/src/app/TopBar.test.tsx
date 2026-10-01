import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BellIcon, BellRingIcon } from 'lucide-react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TopBar } from './TopBar'
import { CommandPalette } from '@/components/CommandPalette'
import type { Me } from '@/lib/types'
import { RouterProvider } from '@/router/useRouter'
import { useAuthStore } from '@/store/auth'
import { useUiStore } from '@/store/ui'

/**
 * 頂列（2026-10-01 改版，規格 `2026-10-01-table-layout.md` §3 與 AC-6）。
 *
 * **這裡不驗版面與配色。** jsdom 沒有佈局也不算樣式，量不出來的東西不該在
 * 這一層假裝驗過；視覺由截圖與 `#/dev/tokens` 驗。這一份只驗行為：
 * 搜尋框開的是同一個 CommandPalette、沒有通知鈴、Viewer 與主題切換在頂列。
 */

/** 只填頂列會讀到的欄位；其餘欄位頂列不碰，用型別斷言帶過。 */
const ME = {
  viewer: { id: 1, google_user_id: 'g-1', email: 'viewer@example.com', display_name: '林示範' },
} as unknown as Me

/**
 * 刻意**不**包 next-themes 的 ThemeProvider：它掛載時會呼叫 `window.matchMedia`，
 * jsdom 沒有這個 API，九則測試會全部在 effect 裡炸掉（2026-10-01 實際踩到）。
 * 沒有 Provider 時 `useTheme()` 拿到的是預設 context（`setTheme` 是 no-op），
 * ThemeToggle 照樣渲染、照樣說得出「目前淺色」——這裡要驗的只是它在頂列上。
 * 切換本身的行為屬於 next-themes，不在這一層驗。
 */
function mount() {
  return render(
    <RouterProvider>
      <TopBar />
      <CommandPalette />
    </RouterProvider>,
  )
}

beforeEach(() => {
  window.history.replaceState(null, '', '#/summary')
  useUiStore.setState({ paletteOpen: false })
  useAuthStore.setState({ me: ME })
})

describe('TopBar', () => {
  it('點搜尋框會開啟 CommandPalette（跟 ⌘K 是同一個面板）', async () => {
    mount()
    expect(screen.queryByRole('dialog', { name: '命令面板' })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: /搜尋 Space、Mention 與設定/ }))

    expect(useUiStore.getState().paletteOpen).toBe(true)
    expect(await screen.findByRole('dialog', { name: '命令面板' })).toBeInTheDocument()
  })

  it('**搜尋框不是另一套頁內搜尋**：它不是輸入框，打不進字', () => {
    // 做成真的 input 就等於第二套搜尋（規格 §3 否決的做法）
    mount()
    const trigger = screen.getByRole('button', { name: /搜尋 Space、Mention 與設定/ })
    expect(trigger.tagName).toBe('BUTTON')
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.queryByRole('searchbox')).toBeNull()
  })

  it('面板關閉後焦點回到搜尋框', async () => {
    mount()
    const trigger = screen.getByRole('button', { name: /搜尋 Space、Mention 與設定/ })
    await userEvent.click(trigger)
    await screen.findByRole('dialog', { name: '命令面板' })
    await waitFor(() => expect(screen.getByRole('combobox', { name: '搜尋指令' })).toHaveFocus())

    await userEvent.keyboard('{Escape}')

    await waitFor(() => expect(trigger).toHaveFocus())
    expect(useUiStore.getState().paletteOpen).toBe(false)
  })

  it('**頂列沒有通知鈴**（規格 §3：不放不會動的佔位元件）', () => {
    const { container } = mount()
    const header = container.querySelector('header')
    expect(header).not.toBeNull()
    expect(screen.queryByRole('button', { name: /通知/ })).toBeNull()
    // lucide 的圖示會帶 `lucide-<名稱>` class；鈴的各種變體都以 bell 開頭
    expect(header!.querySelector('[class*="lucide-bell"]')).toBeNull()
  })

  it('正對照：「沒有鈴」的選擇器真的抓得到 lucide 的鈴', () => {
    // 回報「0 命中」的把關必須先證明它抓得到已知的違規，否則 lucide 哪天
    // 改了 class 命名，上一條會靜默通過
    const { container } = render(
      <header>
        <BellIcon />
        <BellRingIcon />
      </header>,
    )
    expect(container.querySelectorAll('[class*="lucide-bell"]')).toHaveLength(2)
  })

  it('主題切換在頂列，並說得出現在是哪一邊', () => {
    mount()
    const header = screen.getByRole('banner')
    expect(header).toContainElement(screen.getByRole('button', { name: /^目前(淺|深)色，切換為/ }))
  })

  it('右側顯示 Viewer 的名稱與 email', () => {
    mount()
    const header = screen.getByRole('banner')
    expect(header).toHaveTextContent('林示範')
    expect(header).toHaveTextContent('viewer@example.com')
  })

  it('沒有 Viewer（登出中）時不渲染 Viewer 區塊，也不壞', () => {
    useAuthStore.setState({ me: null })
    mount()
    expect(screen.getByRole('banner')).not.toHaveTextContent('viewer@example.com')
  })

  it('標題是當前模組', () => {
    window.history.replaceState(null, '', '#/mentions')
    mount()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Mention 收件匣')
  })

  it('在設定頁時標題是「設定」（設定是主體裡的一頁，頂列照常顯示）', () => {
    window.history.replaceState(null, '', '#/settings/personas')
    mount()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('設定')
  })

  describe('證據鈕（<1280 才有）', () => {
    /** 假裝視窗是 1024–1280：只有 min-width:1024 與 768 成立，證據改走抽屜。 */
    beforeEach(() => {
      vi.stubGlobal('matchMedia', (query: string) => ({
        matches: query !== '(min-width: 1280px)',
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }))
    })
    afterEach(() => {
      vi.unstubAllGlobals()
    })

    it('正對照：工作台上有證據鈕', () => {
      window.history.replaceState(null, '', '#/summary')
      mount()
      expect(screen.getByRole('button', { name: '開啟證據欄' })).toBeInTheDocument()
    })

    it('設定頁沒有證據鈕（設定沒有產出，抽屜也被收起來）', () => {
      window.history.replaceState(null, '', '#/settings/reply')
      mount()
      expect(screen.queryByRole('button', { name: /證據欄/ })).toBeNull()
    })
  })

  it('**渲染結果裡 [title] 選得到 0 個**（規格 2026-09-09 §10.3）', () => {
    const { container } = mount()
    expect(container.querySelectorAll('[title]')).toHaveLength(0)
  })
})
