import { render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useGlobalHotkeys } from './useGlobalHotkeys'
import { SEQUENCE_TIMEOUT_MS } from '@/lib/hotkeys'
import { RouterProvider } from '@/router/useRouter'
import { useRouteSync } from '@/router/useRouteSync'
import { useDraftStore } from '@/store/draft'
import { useMentionsStore } from '@/store/mentions'
import { useSpacesStore } from '@/store/spaces'
import { useSummaryStore } from '@/store/summary'
import { useUiStore } from '@/store/ui'
import type { Mention } from '@/lib/types'

/**
 * 全域快捷鍵的接線（設計規格 §11.1）。
 *
 * 純函式層（「哪個按鍵是哪個動作」）在 `lib/hotkeys.test.ts`；這一份測的是
 * 另外一半——**按下去之後真的呼叫到了對的東西嗎**。那一半在 node 環境測不到，
 * 它需要真的 window 事件與 hash 變動。
 *
 * ### 這裡刻意不測什麼
 *
 * 「⌘Enter 之後畫面長出串流內容」不在這裡：那需要真的 SSE 連線、會燒 AI
 * 配額。這裡只驗「`generate`／`start` 被呼叫、參數對」，串流本身交給既有的
 * `test_tab_switch_streaming.cjs`。同理，瀏覽器 E2E 也刻意不按 ⌘Enter。
 */

function mention(id: number, state: Mention['state'] = 'pending'): Mention {
  return {
    id,
    space_id: 'spaces/x',
    space_name: '（未命名空間）',
    message_name: `spaces/x/messages/${id}`,
    thread_name: null,
    sender_display: '陳柏元',
    // create_time 決定 selectMentionsByState 的排序（降冪），所以 id 越大越新
    create_time: `2026-09-1${id % 10}T02:16:39Z`,
    state,
    resolved_at: null,
  }
}

const toggleEvidence = vi.fn()

/**
 * URL → store 的反向同步。**只有需要連續按兩次快捷鍵的測試才掛它**：
 * 它會把 store 的勾選對齊網址（網址上沒有 merge 就清掉兩則以上的勾選），
 * 那對「勾了合併再按 ⌘Enter」那組測試是干擾。
 */
function Sync() {
  useRouteSync()
  return null
}

function Harness({ sync = false }: { sync?: boolean }) {
  useGlobalHotkeys({ onToggleEvidence: toggleEvidence })
  return (
    <>
      {sync ? <Sync /> : null}
      {/* focus-search 找的是 `aside input`，而「在輸入框裡不觸發單鍵」也要有
          一個真的輸入框當事件來源 */}
      <aside>
        <input aria-label="搜尋 Space" />
      </aside>
    </>
  )
}

function mountAt(hash: string, options: { sync?: boolean } = {}) {
  window.history.replaceState(null, '', hash)
  return render(
    <RouterProvider>
      <Harness sync={options.sync} />
    </RouterProvider>,
  )
}

/** 送一次 keydown。回傳事件本身，好斷言有沒有被 preventDefault。 */
function press(key: string, init: KeyboardEventInit = {}, target: EventTarget = window) {
  const event = new KeyboardEvent('keydown', {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  })
  target.dispatchEvent(event)
  return event
}

const writeText = vi.fn(async () => {})

beforeEach(() => {
  toggleEvidence.mockClear()
  writeText.mockClear()
  // jsdom 沒有 navigator.clipboard，而 copyText 的 textarea 退路依賴
  // document.execCommand（jsdom 也沒有）。補一個假的，讓「複製有沒有被
  // 呼叫」測得到
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
    writable: true,
  })

  useUiStore.setState({ paletteOpen: false, helpOpen: false })
  useSpacesStore.setState({ items: [], selectedId: null })
  useMentionsStore.setState({ items: [], selectedId: null, mergeIds: [], external: null, tab: 'pending' })
  useSummaryStore.setState({ streaming: false, text: '' })
  useDraftStore.setState({ streaming: false, raw: '', replyText: '' })
})

describe('兩鍵序列導覽', () => {
  it('g s 到摘要、g m 到收件匣', () => {
    mountAt('#/mentions')
    press('g')
    press('s')
    expect(window.location.hash).toBe('#/summary')

    press('g')
    press('m')
    expect(window.location.hash).toBe('#/mentions')
  })

  it('g , 到設定、g h 到診斷', () => {
    mountAt('#/summary')
    press('g')
    press(',')
    expect(window.location.hash).toBe('#/settings/reply')
  })

  it('g h 到診斷', () => {
    mountAt('#/summary')
    press('g')
    press('h')
    expect(window.location.hash).toBe('#/settings/diagnostics')
  })

  it('**逾時之後前綴就失效了**', async () => {
    mountAt('#/mentions')
    press('g')
    await new Promise((resolve) => setTimeout(resolve, SEQUENCE_TIMEOUT_MS + 120))
    press('s')
    expect(window.location.hash).toBe('#/mentions')
  })

  it('（正對照）同一組按鍵在逾時之內是會動的', async () => {
    mountAt('#/mentions')
    press('g')
    await new Promise((resolve) => setTimeout(resolve, 50))
    press('s')
    expect(window.location.hash).toBe('#/summary')
  })

  it('中間插一個不相干的鍵，前綴就斷了', () => {
    mountAt('#/mentions')
    press('g')
    press('x')
    press('s')
    expect(window.location.hash).toBe('#/mentions')
  })

  it('**單獨按 Shift 不會把前綴弄斷**', () => {
    // 打 `g` 之後手指碰到 Shift 就整組失效的話，使用者完全看不出原因
    mountAt('#/mentions')
    press('g')
    press('Shift', { shiftKey: true })
    press('s')
    expect(window.location.hash).toBe('#/summary')
  })

  it('在輸入框裡打 g s 只是打字', () => {
    const { container } = mountAt('#/mentions')
    const input = container.querySelector('input')!
    press('g', {}, input)
    press('s', {}, input)
    expect(window.location.hash).toBe('#/mentions')
  })
})

describe('⌘Enter 開始生成', () => {
  it('摘要工作台：帶著選中的 Space 呼叫 start', () => {
    const start = vi.fn(async () => {})
    useSpacesStore.setState({ selectedId: 'spaces/abc' })
    useSummaryStore.setState({ start })
    mountAt('#/summary/abc')

    press('Enter', { metaKey: true })
    expect(start).toHaveBeenCalledWith('spaces/abc')
  })

  it('**正在串流時不重複開始**（正對照：不串流時會呼叫）', () => {
    const start = vi.fn(async () => {})
    useSpacesStore.setState({ selectedId: 'spaces/abc' })
    useSummaryStore.setState({ start, streaming: true })
    mountAt('#/summary/abc')

    press('Enter', { metaKey: true })
    expect(start).not.toHaveBeenCalled()

    useSummaryStore.setState({ streaming: false })
    press('Enter', { metaKey: true })
    expect(start).toHaveBeenCalledTimes(1)
  })

  it('沒選 Space 就不呼叫（只給一句提示，不靜默）', () => {
    const start = vi.fn(async () => {})
    useSummaryStore.setState({ start })
    mountAt('#/summary')

    press('Enter', { metaKey: true })
    expect(start).not.toHaveBeenCalled()
  })

  it('收件匣：帶著選中的 Mention 呼叫 generate', () => {
    const generate = vi.fn(async () => {})
    useMentionsStore.setState({ items: [mention(47)], selectedId: 47 })
    useDraftStore.setState({ generate })
    mountAt('#/mentions/47')

    press('Enter', { metaKey: true })
    expect(generate).toHaveBeenCalledWith(47, [])
  })

  it('**勾了合併就一起帶進去，勾的是別則就不帶**', () => {
    const generate = vi.fn(async () => {})
    useMentionsStore.setState({
      items: [mention(47), mention(48)],
      selectedId: 47,
      mergeIds: [47, 48],
    })
    useDraftStore.setState({ generate })
    mountAt('#/mentions/47')
    press('Enter', { metaKey: true })
    expect(generate).toHaveBeenCalledWith(47, [47, 48])

    // 勾了 48、卻打開 47：不該把 48 一起回掉（與 GenerateButton 同一條規則）
    generate.mockClear()
    useMentionsStore.setState({ mergeIds: [48] })
    press('Enter', { metaKey: true })
    expect(generate).toHaveBeenCalledWith(47, [])
  })

  it('**設定開著時不准開始生成**（那會燒配額）', () => {
    const start = vi.fn(async () => {})
    useSpacesStore.setState({ selectedId: 'spaces/abc' })
    useSummaryStore.setState({ start })
    mountAt('#/settings/reply')

    press('Enter', { metaKey: true })
    expect(start).not.toHaveBeenCalled()
  })
})

describe('⌘. 停止串流', () => {
  it('串流中就中止，並吃掉這個按鍵', () => {
    const abort = vi.fn()
    useSummaryStore.setState({ abort, streaming: true })
    mountAt('#/summary')

    const event = press('.', { metaKey: true })
    expect(abort).toHaveBeenCalled()
    expect(event.defaultPrevented).toBe(true)
  })

  it('**在設定裡也停得掉**（停止是安全閥，不該被覆蓋層擋住）', () => {
    const abort = vi.fn()
    useDraftStore.setState({ abort, streaming: true })
    mountAt('#/settings/reply')

    press('.', { metaKey: true })
    expect(abort).toHaveBeenCalled()
  })

  it('**兩邊都在跑就兩邊都停**（切頁籤不中止生成，看不見的那一半也在燒配額）', () => {
    const summaryAbort = vi.fn()
    const draftAbort = vi.fn()
    useSummaryStore.setState({ abort: summaryAbort, streaming: true })
    useDraftStore.setState({ abort: draftAbort, streaming: true })
    mountAt('#/summary')

    press('.', { metaKey: true })
    expect(summaryAbort).toHaveBeenCalled()
    expect(draftAbort).toHaveBeenCalled()
  })

  it('（正對照）沒在串流時不呼叫 abort，也不吃掉按鍵', () => {
    const abort = vi.fn()
    useSummaryStore.setState({ abort, streaming: false })
    mountAt('#/summary')

    const event = press('.', { metaKey: true })
    expect(abort).not.toHaveBeenCalled()
    expect(event.defaultPrevented).toBe(false)
  })
})

describe('⌘⇧C 複製 Markdown', () => {
  it('摘要視圖複製 summary 的 text', async () => {
    useSummaryStore.setState({ text: '# 摘要\n\n第一點' })
    mountAt('#/summary')

    press('C', { metaKey: true, shiftKey: true })
    await Promise.resolve()
    expect(writeText).toHaveBeenCalledWith('# 摘要\n\n第一點')
  })

  it('草稿視圖優先複製建議回話，沒有就退回全文', async () => {
    useDraftStore.setState({ raw: '## 脈絡分析\n…\n## 建議回話\n好的', replyText: '好的' })
    mountAt('#/mentions/47')

    press('C', { metaKey: true, shiftKey: true })
    await Promise.resolve()
    expect(writeText).toHaveBeenCalledWith('好的')

    writeText.mockClear()
    useDraftStore.setState({ replyText: '' })
    press('C', { metaKey: true, shiftKey: true })
    await Promise.resolve()
    expect(writeText).toHaveBeenCalledWith('## 脈絡分析\n…\n## 建議回話\n好的')
  })

  it('**裸 ⌘C 不可以被攔**（正對照：加了 Shift 才會複製）', async () => {
    useSummaryStore.setState({ text: '# 摘要' })
    mountAt('#/summary')

    const plain = press('c', { metaKey: true })
    await Promise.resolve()
    expect(writeText).not.toHaveBeenCalled()
    expect(plain.defaultPrevented).toBe(false)

    press('C', { metaKey: true, shiftKey: true })
    await Promise.resolve()
    expect(writeText).toHaveBeenCalledTimes(1)
  })
})

describe('[ ] 換 Mention', () => {
  beforeEach(() => {
    // selectMentionsByState 依 create_time 降冪，所以清單順序是 48、47、45
    useMentionsStore.setState({
      items: [mention(45), mention(47), mention(48)],
      selectedId: 47,
    })
  })

  it('**連按 ] 一路往下、再按 [ 走得回來**', async () => {
    // 這一則掛了 useRouteSync：連續按兩次要靠它把 selectedId 對齊網址。
    // 順便證明 `[`／`]` 走的是與點擊同一條路徑（navigate → 反向同步），
    // 沒有繞過 §6.6 的單向同步。
    //
    // **每按一次都要 await**：`navigate` 走 `location.hash = …`，而
    // hashchange 是排到下一個 task 才發的，同一個同步區塊裡 store 還來不及
    // 對齊。第一版沒 await，第二次按 `[` 是從舊的 selectedId 往前算，
    // 於是回到 48 而不是 47——那個紅燈長得像功能壞掉，其實是測試漏了等待。
    mountAt('#/mentions/47', { sync: true })

    press(']')
    expect(window.location.hash).toBe('#/mentions/45')
    await waitFor(() => expect(useMentionsStore.getState().selectedId).toBe(45))

    press('[')
    expect(window.location.hash).toBe('#/mentions/47')
    await waitFor(() => expect(useMentionsStore.getState().selectedId).toBe(47))

    press('[')
    expect(window.location.hash).toBe('#/mentions/48')
  })

  it('**到頭到尾就停住，不繞回去**', () => {
    useMentionsStore.setState({ selectedId: 48 })
    mountAt('#/mentions/48', { sync: true })
    press('[')
    expect(window.location.hash).toBe('#/mentions/48')
  })

  it('已處理分頁只在已處理的那些之間走', () => {
    useMentionsStore.setState({
      items: [mention(45, 'resolved'), mention(47), mention(48, 'resolved')],
      selectedId: 48,
      tab: 'resolved',
    })
    mountAt('#/mentions/48', { sync: true })
    press(']')
    // 47 是 pending，不在這個分頁裡，所以下一則是 45
    expect(window.location.hash).toBe('#/mentions/45')
  })

  it('在輸入框裡是括號，不是換 Mention', () => {
    const { container } = mountAt('#/mentions/47', { sync: true })
    press(']', {}, container.querySelector('input')!)
    expect(window.location.hash).toBe('#/mentions/47')
  })
})

describe('? 快捷鍵說明與整組停用', () => {
  it('? 打開說明面板', () => {
    mountAt('#/summary')
    press('?')
    expect(useUiStore.getState().helpOpen).toBe(true)
  })

  it('（正對照）在輸入框裡按 ? 只是打一個問號', () => {
    const { container } = mountAt('#/summary')
    press('?', {}, container.querySelector('input')!)
    expect(useUiStore.getState().helpOpen).toBe(false)

    press('?')
    expect(useUiStore.getState().helpOpen).toBe(true)
  })

  it('**說明面板開著時全域快捷鍵整組停用**', () => {
    useUiStore.setState({ helpOpen: true })
    mountAt('#/mentions')
    press('g')
    press('s')
    expect(window.location.hash).toBe('#/mentions')
    expect(toggleEvidence).not.toHaveBeenCalled()
  })

  it('命令面板開著時也一樣（既有行為，不要改壞）', () => {
    useUiStore.setState({ paletteOpen: true })
    mountAt('#/summary')
    press('j', { metaKey: true })
    expect(toggleEvidence).not.toHaveBeenCalled()
  })
})
