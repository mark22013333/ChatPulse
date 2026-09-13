import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MentionInbox } from './MentionInbox'
import { RouterProvider } from '@/router/useRouter'
import { useMentionsStore } from '@/store/mentions'
import { useSpacesStore } from '@/store/spaces'
import type { Mention, MentionStateValue } from '@/lib/types'

function mention(id: number, state: MentionStateValue, sender = '陳柏元'): Mention {
  return {
    id,
    space_id: 'spaces/x',
    space_name: '（未命名空間）',
    message_name: `spaces/x/messages/${id}`,
    thread_name: null,
    sender_display: sender,
    create_time: '2026-09-10T02:16:39Z',
    state,
    resolved_at: state === 'resolved' ? '2026-09-10T03:00:00Z' : null,
  }
}

/** 找到某則 Mention 那張卡片，斷言限縮在卡片內——清單裡每則都有一顆同名按鈕。 */
function card(sender: string) {
  return screen.getByText(`${sender} 提到你`).closest('li') as HTMLElement
}

function seed(items: Mention[], tab: 'pending' | 'resolved' = 'pending') {
  useMentionsStore.setState({
    items,
    tab,
    counts: { pending: items.filter((m) => m.state !== 'resolved').length, resolved: 0 },
    loading: false,
    checking: false,
    error: null,
    selectedId: null,
    mergeIds: [],
  })
  useSpacesStore.setState({ items: [] })
}

function renderInbox(onMergedGenerate: (primaryId: number, ids: number[]) => void = () => {}) {
  return render(
    <RouterProvider>
      <MentionInbox onSelect={() => {}} onMergedGenerate={onMergedGenerate} />
    </RouterProvider>,
  )
}

/** 那則卡片上的合併勾選框。 */
function mergeCheckbox(sender: string) {
  return within(card(sender)).getByRole('checkbox')
}

beforeEach(() => {
  window.history.replaceState(null, '', '#/mentions')
  seed([])
})

describe('MentionInbox 的狀態按鈕', () => {
  it('**manual（摘要工作台挑的草稿目標）顯示「標記已處理」**，不是「退回待處理」', () => {
    // 迴歸：manual 在待處理分頁列得出來，按鈕卻寫「退回待處理」；按下去會把
    // state 改成 pending，無聲抹掉「自選對話」這個來源標記，而且不可逆。
    seed([mention(65, 'manual')])
    renderInbox()

    const row = card('陳柏元')
    expect(within(row).getByRole('button', { name: '標記已處理' })).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: '退回待處理' })).toBeNull()
  })

  it('pending 一樣顯示「標記已處理」', () => {
    seed([mention(1, 'pending')])
    renderInbox()

    expect(
      within(card('陳柏元')).getByRole('button', { name: '標記已處理' }),
    ).toBeInTheDocument()
  })

  it('已處理分頁才是「退回待處理」（正對照：這個文案本身沒有消失）', () => {
    seed([mention(9, 'resolved')], 'resolved')
    renderInbox()

    const row = card('陳柏元')
    expect(within(row).getByRole('button', { name: '退回待處理' })).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: '標記已處理' })).toBeNull()
  })

  it('**按 manual 的「標記已處理」送出的是 resolved**，不是 pending', async () => {
    seed([mention(65, 'manual')])
    const setMentionState = vi.fn().mockResolvedValue(undefined)
    useMentionsStore.setState({ setMentionState })
    renderInbox()

    await userEvent.click(within(card('陳柏元')).getByRole('button', { name: '標記已處理' }))

    expect(setMentionState).toHaveBeenCalledWith(65, 'resolved')
  })

  it('manual 出現在待處理分頁、不出現在已處理分頁', () => {
    seed([mention(65, 'manual')], 'resolved')
    renderInbox()

    expect(screen.queryByText('陳柏元 提到你')).toBeNull()
    expect(screen.getByText('還沒有已處理的 Mention。')).toBeInTheDocument()
  })
})

describe('合併勾選寫回網址（?merge=）', () => {
  const A = mention(47, 'pending', '甲')
  const B = mention(48, 'pending', '乙')

  it('**勾兩則之後網址帶得走**，貼給自己也還原得回來', async () => {
    seed([A, B])
    renderInbox()

    await userEvent.click(mergeCheckbox('甲'))
    await userEvent.click(mergeCheckbox('乙'))

    await waitFor(() => expect(window.location.hash).toBe('#/mentions/47?merge=47%2C48'))
  })

  it('順序就是「誰是主要那則」，先勾的排前面', async () => {
    seed([A, B])
    renderInbox()

    await userEvent.click(mergeCheckbox('乙'))
    await userEvent.click(mergeCheckbox('甲'))

    // 回話會送到第一則所在的討論串，所以順序不能被排序或去重打亂
    await waitFor(() => expect(window.location.hash).toBe('#/mentions/48?merge=48%2C47'))
  })

  it('取消勾選之後 merge 從網址上消失', async () => {
    seed([A, B])
    renderInbox()

    await userEvent.click(mergeCheckbox('甲'))
    await userEvent.click(mergeCheckbox('乙'))
    await waitFor(() => expect(window.location.hash).toContain('merge='))

    await userEvent.click(screen.getByRole('button', { name: '取消' }))

    await waitFor(() => expect(window.location.hash).toBe('#/mentions/47'))
  })

  it('切到已處理分頁時勾選被清掉，網址跟著清', async () => {
    seed([A, B])
    renderInbox()

    await userEvent.click(mergeCheckbox('甲'))
    await userEvent.click(mergeCheckbox('乙'))
    await waitFor(() => expect(window.location.hash).toContain('merge='))

    await userEvent.click(screen.getByRole('tab', { name: /已處理/ }))

    await waitFor(() => expect(window.location.hash).toBe('#/mentions/47'))
    expect(useMentionsStore.getState().mergeIds).toEqual([])
  })

  it('**只勾一則不寫進網址**（正對照：勾選狀態本身仍在，只是網址不表達）', async () => {
    // hashForMentions 兩則以上才帶 merge，這是既有的設計決定（route.test.ts
    // 有一條守著）。這條在的意義是確認上面那幾條不是「勾一則就寫」的巧合。
    seed([A, B])
    renderInbox()

    await userEvent.click(mergeCheckbox('甲'))

    await waitFor(() => expect(useMentionsStore.getState().mergeIds).toEqual([47]))
    expect(window.location.hash).toBe('#/mentions/47')
  })
})

/**
 * 清單的方向鍵導航（設計規格 §11.1 的「左欄清單移動」）。
 *
 * 這裡驗的是**焦點**移動，不是選取變化——開啟那一則交給 Enter／點擊。
 * 每按一次方向鍵就導覽的話，走過十則就在歷史裡留下十筆、每一則都重掛草稿
 * 工作區；想快速換一則有 `[`／`]`（測試在 `hooks/useGlobalHotkeys.test.tsx`）。
 */
describe('MentionInbox 的方向鍵導航', () => {
  const 甲 = mention(47, 'pending', '甲')
  const 乙 = mention(48, 'pending', '乙')
  const 丙 = mention(49, 'pending', '丙')

  /** 那則卡片上的主按鈕（就是方向鍵的定位點）。 */
  function cardButton(sender: string) {
    return within(card(sender)).getByRole('button', { name: new RegExp(sender) })
  }

  it('↓ 往下一則、↑ 往上一則', async () => {
    seed([甲, 乙, 丙])
    renderInbox()

    cardButton('甲').focus()
    await userEvent.keyboard('{ArrowDown}')
    expect(cardButton('乙')).toHaveFocus()

    await userEvent.keyboard('{ArrowDown}')
    expect(cardButton('丙')).toHaveFocus()

    await userEvent.keyboard('{ArrowUp}')
    expect(cardButton('乙')).toHaveFocus()
  })

  it('Home 與 End 跳到頭尾，邊界夾住不繞回', async () => {
    seed([甲, 乙, 丙])
    renderInbox()

    cardButton('甲').focus()
    await userEvent.keyboard('{End}')
    expect(cardButton('丙')).toHaveFocus()

    // 已經在最後一則，再按 ↓ 停在原地（不繞回第一則）
    await userEvent.keyboard('{ArrowDown}')
    expect(cardButton('丙')).toHaveFocus()

    await userEvent.keyboard('{Home}')
    expect(cardButton('甲')).toHaveFocus()
    await userEvent.keyboard('{ArrowUp}')
    expect(cardButton('甲')).toHaveFocus()
  })

  it('**焦點在勾選框上時方向鍵不跳走**', async () => {
    // 勾選框自己不吃方向鍵，但也不該讓清單搶走——使用者是在操作那個勾選框
    seed([甲, 乙])
    renderInbox()

    mergeCheckbox('甲').focus()
    await userEvent.keyboard('{ArrowDown}')
    expect(mergeCheckbox('甲')).toHaveFocus()
  })

  it('**方向鍵不改變選取**（正對照：點擊才會）', async () => {
    // 每按一次方向鍵就導覽，走過十則就是十筆歷史。移動與開啟要分開
    const selected: number[] = []
    seed([甲, 乙])
    render(
      <RouterProvider>
        <MentionInbox onSelect={(id) => selected.push(id)} onMergedGenerate={() => {}} />
      </RouterProvider>,
    )

    cardButton('甲').focus()
    await userEvent.keyboard('{ArrowDown}')
    expect(selected).toEqual([])

    // 正對照：同一顆按鈕按 Enter（卡片本身就是 button）就會開啟
    await userEvent.keyboard('{Enter}')
    expect(selected).toEqual([48])
  })

  it('打字鍵不被吃掉（清單不該攔下不是導航鍵的按鍵）', async () => {
    seed([甲, 乙])
    renderInbox()

    cardButton('甲').focus()
    const event = new KeyboardEvent('keydown', { key: 'a', bubbles: true, cancelable: true })
    cardButton('甲').dispatchEvent(event)
    expect(event.defaultPrevented).toBe(false)
  })
})
