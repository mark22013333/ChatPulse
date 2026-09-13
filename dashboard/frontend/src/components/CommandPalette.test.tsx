import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { CommandPalette } from './CommandPalette'
import { useGlobalHotkeys } from '@/hooks/useGlobalHotkeys'
import { RouterProvider } from '@/router/useRouter'
import { useMentionsStore } from '@/store/mentions'
import { useSpacesStore } from '@/store/spaces'
import { useUiStore } from '@/store/ui'
import type { Mention, Space } from '@/lib/types'

/**
 * 設計規格 §15.4 的第 4 條：命令面板。
 *
 * 面板本身把 ⌘K 的開啟交給 `useGlobalHotkeys`，所以測試把兩者一起掛
 * ——分開測的話「單按 K 不能開」這條會落在兩個檔案的縫隙裡，
 * 而那正是最容易壞掉的地方（打字時被面板打斷）。
 */

const SPACES: Space[] = [
  {
    id: 'spaces/WCS',
    displayName: 'WCS 倉儲系統',
    type: 'SPACE',
    lastActiveTime: '2026-09-10T02:00:00Z',
    renamable: false,
    nameSource: null,
    pinned: true,
  },
  {
    id: 'spaces/HR',
    displayName: '人資公告',
    type: 'SPACE',
    lastActiveTime: '2026-09-09T02:00:00Z',
    renamable: false,
    nameSource: null,
    pinned: false,
  },
]

const MENTIONS: Mention[] = [
  {
    id: 45,
    space_id: 'spaces/WCS',
    space_name: 'WCS 倉儲系統',
    message_name: 'spaces/WCS/messages/45',
    thread_name: null,
    sender_display: '陳柏元',
    create_time: '2026-09-10T02:16:39Z',
    state: 'pending',
    resolved_at: null,
  },
]

/** 照 App.tsx 的方式掛：全域快捷鍵 ＋ 面板，另有一個 textarea 當打字現場。 */
function Harness() {
  useGlobalHotkeys({ onToggleEvidence: () => {} })
  return (
    <>
      <textarea aria-label="建議回話" defaultValue="" />
      <CommandPalette />
    </>
  )
}

function mount() {
  return render(
    <RouterProvider>
      <Harness />
    </RouterProvider>,
  )
}

const palette = () => screen.queryByRole('dialog', { name: '命令面板' })
const searchBox = () => screen.getByRole('combobox', { name: '搜尋指令' })
const options = () => screen.getAllByRole('option')
const activeOption = () => options().find((el) => el.getAttribute('aria-selected') === 'true')

beforeEach(() => {
  window.history.replaceState(null, '', '#/summary')
  useUiStore.setState({ paletteOpen: false })
  useSpacesStore.setState({ items: SPACES })
  useMentionsStore.setState({ items: MENTIONS })
})

describe('開與關', () => {
  it('**⌘K 開啟，焦點進搜尋框**', async () => {
    mount()
    expect(palette()).toBeNull()

    await userEvent.keyboard('{Meta>}k{/Meta}')

    await waitFor(() => expect(palette()).toBeInTheDocument())
    await waitFor(() => expect(document.activeElement).toBe(searchBox()))
  })

  it('Ctrl+K 也開（不是只認 Mac 的 ⌘）', async () => {
    mount()

    await userEvent.keyboard('{Control>}k{/Control}')

    await waitFor(() => expect(palette()).toBeInTheDocument())
  })

  it('**Esc 關閉，而且把焦點還回原本的地方**', async () => {
    mount()
    const textarea = screen.getByRole('textbox', { name: '建議回話' })
    textarea.focus()

    await userEvent.keyboard('{Meta>}k{/Meta}')
    await waitFor(() => expect(document.activeElement).toBe(searchBox()))

    await userEvent.keyboard('{Escape}')

    expect(palette()).toBeNull()
    // 不還焦點的話，鍵盤使用者會被丟回頁面頂端，剛才打到一半的回話就找不回來
    expect(document.activeElement).toBe(textarea)
  })
})

describe('在輸入框裡打字不會被打斷', () => {
  it('**textarea 裡按 ⌘K 還是要開**（那是刻意的例外）', async () => {
    mount()
    const textarea = screen.getByRole('textbox', { name: '建議回話' })
    textarea.focus()

    await userEvent.keyboard('{Meta>}k{/Meta}')

    await waitFor(() => expect(palette()).toBeInTheDocument())
  })

  it('**textarea 裡單按 k 不能開**——不然打字就變成在下指令', async () => {
    mount()
    const textarea = screen.getByRole('textbox', { name: '建議回話' })
    textarea.focus()

    await userEvent.keyboard('kkk')

    expect(palette()).toBeNull()
    // 正對照：那三個字真的進到 textarea 了，證明按鍵有送達
    expect(textarea).toHaveValue('kkk')
  })
})

describe('過濾與選取', () => {
  it('打字會過濾，Space 與 Mention 都找得到', async () => {
    mount()
    await userEvent.keyboard('{Meta>}k{/Meta}')
    await waitFor(() => expect(palette()).toBeInTheDocument())

    await userEvent.type(searchBox(), 'WCS')

    await waitFor(() => expect(options().length).toBeGreaterThan(0))
    const text = options()
      .map((el) => el.textContent ?? '')
      .join('|')
    expect(text).toContain('WCS 倉儲系統')
    // 不相關的 Space 要被濾掉
    expect(text).not.toContain('人資公告')
  })

  it('查無結果時說得出「查不到什麼」', async () => {
    mount()
    await userEvent.keyboard('{Meta>}k{/Meta}')
    await waitFor(() => expect(palette()).toBeInTheDocument())

    await userEvent.type(searchBox(), 'zzzz沒有這個東西')

    expect(await screen.findByText(/找不到「zzzz沒有這個東西」/)).toBeInTheDocument()
  })

  it('**↑↓ 移動高亮，但焦點不離開輸入框**', async () => {
    mount()
    await userEvent.keyboard('{Meta>}k{/Meta}')
    await waitFor(() => expect(palette()).toBeInTheDocument())

    const first = activeOption()?.id
    await userEvent.keyboard('{ArrowDown}')
    const second = activeOption()?.id
    expect(second).not.toBe(first)

    // 焦點留在輸入框是這個設計的重點：移動真實焦點會中斷打字
    expect(document.activeElement).toBe(searchBox())
    // aria-activedescendant 才是告訴螢幕閱讀器「現在在哪一項」的東西
    expect(searchBox()).toHaveAttribute('aria-activedescendant', second)

    await userEvent.keyboard('{ArrowUp}')
    expect(activeOption()?.id).toBe(first)
  })

  it('Enter 執行高亮那一項並關閉面板', async () => {
    mount()
    await userEvent.keyboard('{Meta>}k{/Meta}')
    await waitFor(() => expect(palette()).toBeInTheDocument())
    await userEvent.type(searchBox(), 'WCS 倉儲')
    await waitFor(() => expect(options().length).toBeGreaterThan(0))

    await userEvent.keyboard('{Enter}')

    await waitFor(() => expect(palette()).toBeNull())
    expect(window.location.hash).not.toBe('#/summary')
  })
})

describe('輸入法組字（台灣同事的預設輸入法是注音）', () => {
  it('**組字中按 Enter 不得執行命令**——那時 Enter 的意思是「選這個字」', async () => {
    mount()
    await userEvent.keyboard('{Meta>}k{/Meta}')
    await waitFor(() => expect(palette()).toBeInTheDocument())
    const before = window.location.hash

    // userEvent 送不出 isComposing，所以直接派一個帶 isComposing 的原生事件
    fireEvent.keyDown(searchBox(), { key: 'Enter', isComposing: true })

    expect(palette()).toBeInTheDocument()
    expect(window.location.hash).toBe(before)
  })

  it('keyCode 229（Safari 舊版不設 isComposing）也要擋', async () => {
    mount()
    await userEvent.keyboard('{Meta>}k{/Meta}')
    await waitFor(() => expect(palette()).toBeInTheDocument())
    const before = window.location.hash

    fireEvent.keyDown(searchBox(), { key: 'Enter', keyCode: 229 })

    expect(palette()).toBeInTheDocument()
    expect(window.location.hash).toBe(before)
  })

  it('正對照：沒在組字時同一個 Enter 確實會執行', async () => {
    mount()
    await userEvent.keyboard('{Meta>}k{/Meta}')
    await waitFor(() => expect(palette()).toBeInTheDocument())

    fireEvent.keyDown(searchBox(), { key: 'Enter' })

    await waitFor(() => expect(palette()).toBeNull())
  })
})

describe('不可撤回的動作不進面板', () => {
  it('搜「送出」找不到任何指令', async () => {
    // 打字 → Enter 是最容易誤觸的介面，而送出回話不可撤回。
    // lib/commands.test.ts 從資料層守著，這條從畫面上再確認一次。
    mount()
    await userEvent.keyboard('{Meta>}k{/Meta}')
    await waitFor(() => expect(palette()).toBeInTheDocument())

    await userEvent.type(searchBox(), '送出')

    expect(await screen.findByText(/找不到「送出」/)).toBeInTheDocument()
  })
})
