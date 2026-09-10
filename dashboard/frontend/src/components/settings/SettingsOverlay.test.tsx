import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SettingsOverlay } from './SettingsOverlay'
import { CommandPalette } from '@/components/CommandPalette'
import { hashForSettings } from '@/lib/route'
import { RouterProvider, useRouter } from '@/router/useRouter'
import { useUiStore } from '@/store/ui'

// 六個設定頁各自會載入自己的資料，與「關閉要退到哪」無關。
// 換成佔位元件，測試才只在量覆蓋層的歷史行為。
vi.mock('@/components/settings/ReplyDefaultsPage', () => ({
  ReplyDefaultsPage: () => <div>回覆預設值內容</div>,
}))
vi.mock('@/components/settings/PersonasPage', () => ({
  PersonasPage: () => <div>Persona 內容</div>,
}))
vi.mock('@/components/settings/ReplyPromptsPage', () => ({
  ReplyPromptsPage: () => <div>常用提示詞內容</div>,
}))
vi.mock('@/components/settings/CodeProjectsPage', () => ({
  CodeProjectsPage: () => <div>參考專案內容</div>,
}))
vi.mock('@/components/settings/SpacePrefsPage', () => ({
  SpacePrefsPage: () => <div>Space 內容</div>,
}))
vi.mock('@/components/settings/DataPage', () => ({
  DataPage: () => <div>資料內容</div>,
}))

/** 照 App.tsx 的方式掛：設定是覆蓋層，路由落在 settings 時才渲染。 */
function Harness() {
  const { route, navigate } = useRouter()
  return (
    <>
      <button type="button" onClick={() => navigate(hashForSettings('reply'))}>
        開啟設定
      </button>
      {route.section === 'settings' ? <SettingsOverlay /> : null}
    </>
  )
}

function mount(initialHash: string) {
  window.history.replaceState(null, '', initialHash)
  return render(
    <RouterProvider>
      <Harness />
    </RouterProvider>,
  )
}

/** 設定 ＋ 真正的命令面板一起掛，用來測 Esc 的層次。 */
function mountWithPalette(initialHash: string) {
  window.history.replaceState(null, '', initialHash)
  return render(
    <RouterProvider>
      <Harness />
      <CommandPalette />
    </RouterProvider>,
  )
}

/**
 * 分頁的可及名稱是「標題＋說明」兩段接起來的，所以要錨在開頭比對：
 * 光用 /Persona/ 會同時命中「回覆預設值」那一列（它的說明寫著「口氣、Persona…」）。
 */
const TAB_NAMES = [/^Persona/, /^常用提示詞/, /^參考專案/, /^Space/, /^資料/] as const

function tabLink(name: RegExp) {
  return screen.getByRole('link', { name })
}

/** 依序點過五個分頁。回傳點完之後 history.length 的增量。 */
async function clickFiveTabs(): Promise<number> {
  const before = window.history.length
  for (const name of TAB_NAMES) {
    await userEvent.click(tabLink(name))
  }
  return window.history.length - before
}

beforeEach(() => {
  useUiStore.setState({ paletteOpen: false })
  window.history.replaceState(null, '', '#/summary')
})

describe('設定覆蓋層的關閉路徑', () => {
  it('**正對照：一次 push 導覽會讓 history.length +1**', async () => {
    mount('#/mentions/65')
    const before = window.history.length

    await userEvent.click(screen.getByRole('button', { name: '開啟設定' }))
    await waitFor(() => expect(window.location.hash).toBe('#/settings/reply'))

    // 這條在的意義：下一則測試量到「切五個分頁 +0」時，那個 0 才不是
    // 「history.length 在 jsdom 根本不會動」的假象。
    expect(window.history.length - before).toBe(1)
  })

  it('**切五個分頁只占一筆歷史**（點 N 個分頁不該要按 N 次關閉）', async () => {
    mount('#/settings/reply')

    const delta = await clickFiveTabs()

    expect(window.location.hash).toBe('#/settings/data')
    expect(delta).toBe(0)
  })

  it('**點過五個分頁之後，按一次「關閉」就回到原本的位置**', async () => {
    mount('#/mentions/65')
    await userEvent.click(screen.getByRole('button', { name: '開啟設定' }))
    await waitFor(() => expect(window.location.hash).toBe('#/settings/reply'))
    await clickFiveTabs()

    await userEvent.click(screen.getByRole('button', { name: '關閉' }))

    await waitFor(() => expect(window.location.hash).toBe('#/mentions/65'))
    expect(screen.queryByRole('dialog', { name: '設定' })).toBeNull()
  })

  it('關閉用 replace，整段設定操作在歷史上只留一筆', async () => {
    mount('#/mentions/65')
    const before = window.history.length

    await userEvent.click(screen.getByRole('button', { name: '開啟設定' }))
    await waitFor(() => expect(window.location.hash).toBe('#/settings/reply'))
    await clickFiveTabs()
    await userEvent.click(screen.getByRole('button', { name: '關閉' }))
    await waitFor(() => expect(window.location.hash).toBe('#/mentions/65'))

    // 進設定 push 一筆、關閉把那一筆換掉 → 淨增量 1。若關閉沒帶 replace，
    // 這裡會是 2，且按返回鍵會掉回設定裡。
    expect(window.history.length - before).toBe(1)
  })

  it('直接貼設定連結進來，關閉落到 #/summary（不是白畫面）', async () => {
    mount('#/settings/personas')
    expect(screen.getByRole('dialog', { name: '設定' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '關閉' }))

    await waitFor(() => expect(window.location.hash).toBe('#/summary'))
  })

  it('Esc 與「關閉」按鈕行為一致', async () => {
    mount('#/mentions/65')
    await userEvent.click(screen.getByRole('button', { name: '開啟設定' }))
    await waitFor(() => expect(window.location.hash).toBe('#/settings/reply'))
    await clickFiveTabs()

    await userEvent.keyboard('{Escape}')

    await waitFor(() => expect(window.location.hash).toBe('#/mentions/65'))
  })

  it('**命令面板開著時，Esc 只關面板、不關設定**（由內而外）', async () => {
    // 這一則一定要掛**真正的** CommandPalette。只設 store 旗標的話測的是
    // 「守衛讀不讀 store」，測不到真實時序：面板的 close() 是同步的 zustand
    // set，所以事件冒泡到設定的 window 監聽器時 paletteOpen 已經是 false。
    // 2026-09-10 就是這樣給出假綠燈，由真瀏覽器 E2E 才抓到設定被一起關掉。
    mountWithPalette('#/settings/reply')
    act(() => useUiStore.setState({ paletteOpen: true }))
    expect(screen.getByRole('dialog', { name: '命令面板' })).toBeInTheDocument()

    await userEvent.keyboard('{Escape}')

    expect(screen.queryByRole('dialog', { name: '命令面板' })).toBeNull()
    expect(screen.getByRole('dialog', { name: '設定' })).toBeInTheDocument()
    expect(window.location.hash).toBe('#/settings/reply')

    // 正對照：面板關掉之後同一個按鍵確實關得掉設定
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(window.location.hash).toBe('#/summary'))
  })

  it('面板開著但按鍵不是它處理的（焦點在外面）時，設定也不該關', async () => {
    // 面板的 Esc handler 掛在輸入框上。焦點跑到外面時它不會觸發，
    // 於是 defaultPrevented 是 false——這時就要靠 paletteOpen 那道守衛。
    mountWithPalette('#/settings/reply')
    act(() => useUiStore.setState({ paletteOpen: true }))

    // 直接對 window 派事件，模擬「焦點不在面板輸入框上」
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })

    expect(window.location.hash).toBe('#/settings/reply')
    expect(screen.getByRole('dialog', { name: '設定' })).toBeInTheDocument()
  })

  it('⌘＋點擊分頁不攔截，交還瀏覽器原生行為（開新分頁）', () => {
    mount('#/settings/reply')

    // jsdom 沒有「開新分頁」語意，⌘＋點擊只會跟著 href 走，所以這裡不能量
    // hash，要量的是**有沒有被攔截**——那才是這段程式碼的契約。
    // fireEvent 回傳 false 代表事件被 preventDefault。
    expect(fireEvent.click(tabLink(/^Persona/), { metaKey: true })).toBe(true)

    // 正對照：一般左鍵確實有被攔截。少了這條，上面那個 true 也可能只是
    // 「handler 根本沒掛上」。
    expect(fireEvent.click(tabLink(/^常用提示詞/))).toBe(false)
  })
})
