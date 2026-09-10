import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DraftReplyWorkspace } from '@/components/draft/DraftReplyWorkspace'
import { RouterProvider } from '@/router/useRouter'
import { useCodeProjectStore } from '@/store/codeProjects'
import { useDraftStore } from '@/store/draft'
import { useMentionsStore } from '@/store/mentions'
import { useReplySettingsStore } from '@/store/replySettings'
import { useSpacesStore } from '@/store/spaces'
import type { Mention, SseMeta } from '@/lib/types'

/**
 * 設計規格 §15.4 的第 1 條：送出流程。
 *
 * 為什麼這一條排在四條的第一位——**這個產品最貴的錯誤是「送出了不可撤回的
 * 訊息」**。回話以 Viewer 本人的身分發出，ChatPulse 沒有撤回。而「漏回其中
 * 一則」從草稿內容本身完全看不出來，只能靠確認框那份名單。
 *
 * 這些行為全都是條件渲染，store 測試看不到。
 */

const MENTION: Mention = {
  id: 45,
  space_id: 'spaces/x',
  space_name: '工程討論',
  message_name: 'spaces/x/messages/45',
  thread_name: null,
  sender_display: '陳柏元',
  create_time: '2026-09-10T02:16:39Z',
  state: 'pending',
  resolved_at: null,
}

/** meta.answering 有三則：這一送會一次結掉三則 Mention。 */
const META_THREE: SseMeta = {
  mention_id: 45,
  answering: [
    { mention_id: 45, sender_display: '陳柏元', create_time: '2026-09-10T02:16:39Z' },
    { mention_id: 46, sender_display: '林小美', create_time: '2026-09-10T03:20:00Z' },
    { mention_id: 47, sender_display: '王大文', create_time: '2026-09-10T04:05:00Z' },
  ],
} as SseMeta

function seedStores() {
  // loaded: true 讓 ensureLoaded／load 早退，測試不打 API
  useCodeProjectStore.setState({ projects: [], loaded: true, loading: false })
  useReplySettingsStore.setState({
    tones: [],
    personas: [],
    replyPrompts: [],
    polishers: [],
    loaded: true,
    load: async () => {},
  })
  useSpacesStore.setState({ items: [] })
  useMentionsStore.setState({ items: [], mergeIds: [], selectedId: 45, external: null })
  useDraftStore.setState({
    referenceSpaceIds: [],
    codeRefs: [],
    referenceSearch: '',
    refLimitError: null,
    streaming: false,
    raw: '### ✍️ 建議回話\n這邊我確認過了。',
    meta: null,
    error: null,
    polish: null,
    replyText: '這邊我確認過了。',
    replyEdited: false,
    sending: false,
    mentionId: 45,
  })
}

function renderWorkspace() {
  return render(
    <RouterProvider>
      <DraftReplyWorkspace mention={MENTION} />
    </RouterProvider>,
  )
}

const sendButton = () => screen.getByRole('button', { name: '送出回話' })

async function openConfirm() {
  await userEvent.click(sendButton())
  return screen.findByRole('dialog')
}

beforeEach(() => {
  window.history.replaceState(null, '', '#/mentions/45')
  seedStores()
})

describe('送出流程：什麼時候送不出去', () => {
  it('**回話空白時送不出去**', () => {
    useDraftStore.setState({ replyText: '   ' })
    renderWorkspace()

    expect(sendButton()).toBeDisabled()
  })

  it('**串流中送不出去**（內容還在長，這時候送出的是半截草稿）', () => {
    useDraftStore.setState({ streaming: true })
    renderWorkspace()

    expect(sendButton()).toBeDisabled()
  })

  it('送出進行中送不出去（避免連按兩次送兩則）', () => {
    useDraftStore.setState({ sending: true })
    renderWorkspace()

    expect(sendButton()).toBeDisabled()
  })

  it('正對照：有內容、沒串流、沒在送 → 送得出去', () => {
    renderWorkspace()

    expect(sendButton()).toBeEnabled()
  })
})

describe('送出確認框', () => {
  it('**answering 有 3 則時，逐則列出寄件人與時間**', async () => {
    // 只講數字不夠：送出不可撤回，而「漏回其中一則」從草稿內容看不出來。
    // 這份名單在改版之前只活在一個 title 屬性裡，鍵盤與觸控使用者拿不到。
    useDraftStore.setState({ meta: META_THREE })
    renderWorkspace()

    const dialog = await openConfirm()

    expect(within(dialog).getByText('陳柏元')).toBeInTheDocument()
    expect(within(dialog).getByText('林小美')).toBeInTheDocument()
    expect(within(dialog).getByText('王大文')).toBeInTheDocument()
    // 時間也要在——同一個人連問兩件事時，名字分不出是哪一則
    expect(within(dialog).getAllByText(/2026/)).toHaveLength(3)
    // 那句話被 <strong> 切成幾段，所以用 textContent 比對整段而不是單一節點
    expect(within(dialog).getByText('3 則')).toBeInTheDocument()
    expect(dialog.textContent).toContain('會一起標成已處理')
  })

  it('只回一則時不列名單，改說「這則會自動變成已處理」', async () => {
    useDraftStore.setState({
      meta: { mention_id: 45, answering: [{ mention_id: 45 }] } as SseMeta,
    })
    renderWorkspace()

    const dialog = await openConfirm()

    expect(within(dialog).getByText(/送出後這則 Mention 會自動變成已處理/)).toBeInTheDocument()
    expect(within(dialog).queryByText(/會一起標成已處理/)).toBeNull()
  })

  it('確認框帶回話全文預覽（按下確認之前看得到會送出什麼）', async () => {
    renderWorkspace()

    const dialog = await openConfirm()

    expect(within(dialog).getByText('回話全文預覽')).toBeInTheDocument()
    expect(within(dialog).getByText('這邊我確認過了。')).toBeInTheDocument()
    expect(within(dialog).getByText('工程討論')).toBeInTheDocument()
  })
})

describe('送出的結果', () => {
  it('**送出失敗時對話框不關**——關掉的話使用者會以為送成功了', async () => {
    const send = vi.fn().mockRejectedValue(new Error('討論串已被刪除'))
    useDraftStore.setState({ send })
    const applyResolved = vi.fn()
    useMentionsStore.setState({ applyResolved })
    renderWorkspace()

    const dialog = await openConfirm()
    await userEvent.click(within(dialog).getByRole('button', { name: '確認送出' }))

    await waitFor(() => expect(send).toHaveBeenCalledWith(45))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    // 沒送出去就不可以標成已處理
    expect(applyResolved).not.toHaveBeenCalled()
  })

  it('正對照：送出成功會關掉對話框並標記已處理', async () => {
    const resolved: Mention = { ...MENTION, state: 'resolved', resolved_at: '2026-09-10T05:00:00Z' }
    const send = vi.fn().mockResolvedValue([resolved])
    useDraftStore.setState({ send })
    const applyResolvedMany = vi.fn()
    useMentionsStore.setState({ applyResolvedMany })
    renderWorkspace()

    const dialog = await openConfirm()
    await userEvent.click(within(dialog).getByRole('button', { name: '確認送出' }))

    await waitFor(() => expect(applyResolvedMany).toHaveBeenCalledWith([resolved]))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('送出中確認鈕會鎖住（連按兩次不會送兩則）', async () => {
    useDraftStore.setState({ sending: false })
    renderWorkspace()
    const dialog = await openConfirm()

    // 進入送出中：確認與取消都要鎖，不然對話框關掉、請求還在飛
    useDraftStore.setState({ sending: true })

    await waitFor(() =>
      expect(within(dialog).getByRole('button', { name: '確認送出' })).toBeDisabled(),
    )
    expect(within(dialog).getByRole('button', { name: '取消' })).toBeDisabled()
  })
})

describe('原始 Mention 卡：三種狀態不能只靠顏色分辨', () => {
  it('pending 顯示「待處理」', () => {
    renderWorkspace()
    expect(screen.getByText('待處理')).toBeInTheDocument()
  })

  it('**manual 顯示「自選對話」並說明它為什麼不在收件匣**', () => {
    // --verified 與 --signal 刻意是同一個色，所以三態一定要有文字。
    // 這段說明原本只活在 badge 的 tooltip 裡，鍵盤與觸控使用者拿不到。
    render(
      <RouterProvider>
        <DraftReplyWorkspace mention={{ ...MENTION, state: 'manual' }} />
      </RouterProvider>,
    )

    expect(screen.getByText('自選對話')).toBeInTheDocument()
    expect(screen.getByText(/你從摘要工作台挑的對話/)).toBeInTheDocument()
  })

  it('resolved 顯示「✓ 已處理」', () => {
    render(
      <RouterProvider>
        <DraftReplyWorkspace mention={{ ...MENTION, state: 'resolved' }} />
      </RouterProvider>,
    )

    expect(screen.getByText('✓ 已處理')).toBeInTheDocument()
    expect(screen.queryByText(/你從摘要工作台挑的對話/)).toBeNull()
  })

  it('取不回訊息內容時說出原因，不是留白', () => {
    render(
      <RouterProvider>
        <DraftReplyWorkspace
          mention={{ ...MENTION, text: undefined, content_error: '權限不足' }}
        />
      </RouterProvider>,
    )

    expect(screen.getByText(/無法取回訊息內容：權限不足/)).toBeInTheDocument()
  })
})

describe('產生鈕', () => {
  it('沒有草稿時是「產生 Draft Reply」，有了之後變「重新產生」', () => {
    useDraftStore.setState({ raw: '' })
    const { unmount } = renderWorkspace()
    expect(screen.getByRole('button', { name: /^產生 Draft Reply/ })).toBeInTheDocument()
    unmount()

    useDraftStore.setState({ raw: '### ✍️ 建議回話\n有內容了' })
    renderWorkspace()
    expect(screen.getByRole('button', { name: /重新產生 Draft Reply/ })).toBeInTheDocument()
  })

  it('**收件匣勾了要合併時，按鈕要說出會合併幾則**', () => {
    // 「勾了兩則卻只回到一則」是靜默的，使用者要送出後才發現
    useMentionsStore.setState({ mergeIds: [45, 46] })
    renderWorkspace()

    expect(screen.getByRole('button', { name: /合併 2 則/ })).toBeInTheDocument()
  })

  it('勾選裡沒有這一則時不算合併（不會把不相干的一起回掉）', () => {
    useMentionsStore.setState({ mergeIds: [99, 100] })
    renderWorkspace()

    expect(screen.queryByRole('button', { name: /合併/ })).toBeNull()
  })

  it('串流中換成「停止串流」', () => {
    useDraftStore.setState({ streaming: true })
    renderWorkspace()

    expect(screen.getByRole('button', { name: '停止串流' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /產生 Draft Reply/ })).toBeNull()
  })

  it('抓取則數不合法時產生鈕鎖住', () => {
    useDraftStore.setState({ refLimitError: '抓取則數必須介於 1 至 1000 之間' })
    renderWorkspace()

    expect(screen.getByRole('button', { name: /產生 Draft Reply/ })).toBeDisabled()
  })
})

describe('Reference Space 與參考專案的控件', () => {
  it('已勾選數量與清空鈕', async () => {
    useDraftStore.setState({ referenceSpaceIds: ['spaces/a', 'spaces/b'] })
    renderWorkspace()

    expect(screen.getByText('已勾選 2')).toBeInTheDocument()
    await userEvent.click(screen.getAllByRole('button', { name: '清空' })[0])
    expect(useDraftStore.getState().referenceSpaceIds).toEqual([])
  })

  it('**抓取則數的錯誤訊息是 role="alert" 並被輸入框指名**', () => {
    // 只有 aria-invalid 的話，螢幕閱讀器讀得出「這欄有問題」但讀不到
    // 「問題是什麼」（規格 §10.7）
    useDraftStore.setState({ refLimitError: '抓取則數必須是整數' })
    renderWorkspace()

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('抓取則數必須是整數')
    expect(screen.getByLabelText(/每群抓取則數/)).toHaveAttribute(
      'aria-describedby',
      alert.id,
    )
  })

  it('一個參考專案都沒設定時整塊不畫（不是畫一個空清單）', () => {
    useCodeProjectStore.setState({ projects: [], loaded: true })
    renderWorkspace()

    expect(screen.queryByText('參考專案')).toBeNull()
  })

  it('有參考專案時列出環境勾選', () => {
    useCodeProjectStore.setState({
      projects: [
        {
          id: 3,
          name: 'WCS',
          branches: { production: 'main', uat: 'uat', dev: '' },
        } as never,
      ],
      loaded: true,
    })
    renderWorkspace()

    expect(screen.getByText('參考專案')).toBeInTheDocument()
    expect(screen.getByText('WCS')).toBeInTheDocument()
    expect(screen.getByText('main')).toBeInTheDocument()
  })
})

describe('Sepia 潤稿被退回時要明說原因', () => {
  it('**fallback_reason 是畫面上讀得到的文字**，不是 title 屬性', () => {
    // 使用者需要知道「是哪個事實被改動了」，那是判斷「模型在亂改」還是
    // 「檢查太嚴」的唯一依據。放進 title 等於鍵盤與觸控使用者拿不到。
    useDraftStore.setState({
      polish: { polished: false, fallback_reason: '數字 47 被改成 48' } as never,
    })
    renderWorkspace()

    expect(screen.getByText('Sepia 潤稿未採用')).toBeInTheDocument()
    expect(screen.getByText('數字 47 被改成 48')).toBeInTheDocument()
    expect(
      screen.getByText(/下面顯示的是未潤稿的版本，內容仍然可以直接送出/),
    ).toBeInTheDocument()
  })

  it('正對照：潤稿採用時不出現那條警語', () => {
    useDraftStore.setState({ polish: { polished: true, fallback_reason: null } as never })
    renderWorkspace()

    expect(screen.queryByText('Sepia 潤稿未採用')).toBeNull()
  })
})
