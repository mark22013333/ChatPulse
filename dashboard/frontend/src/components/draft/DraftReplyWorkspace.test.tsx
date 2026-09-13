import { act, render, screen, waitFor, within } from '@testing-library/react'
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

/** 有一個參考專案（正式／UAT 兩個環境）的狀態。 */
function seedCodeProject() {
  useCodeProjectStore.setState({
    projects: [
      { id: 3, name: 'WCS', branches: { production: 'main', uat: 'uat', dev: '' } } as never,
    ],
    loaded: true,
  })
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

  it('**ConfirmDialog 的 title prop 不會變成 DOM 的 tooltip 屬性**', async () => {
    // tokens.test.ts 的白名單留了這兩處，理由是「那是對話框標題不是 tooltip」。
    // 這條就是那個理由的證明——渲染結果裡一個 title 屬性都不該有。
    renderWorkspace()
    await openConfirm()

    expect(document.querySelectorAll('[title]')).toHaveLength(0)
    // 正對照：標題本身是讀得到的文字
    expect(screen.getByText('送出這則回話？')).toBeInTheDocument()
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
    seedCodeProject()
    renderWorkspace()

    expect(screen.getByText('參考專案')).toBeInTheDocument()
    expect(screen.getByText('WCS')).toBeInTheDocument()
    expect(screen.getByText('main')).toBeInTheDocument()
  })
})

describe('手動指定檢索關鍵字（ADR-0006 的逃生門）', () => {
  const termsBox = () => screen.queryByLabelText(/自己指定檢索關鍵字/)

  it('**沒勾任何專案時不出現這個輸入框**', () => {
    // 沒勾專案的話後端根本不會搜，多一個沒作用的輸入框只會讓人困惑
    seedCodeProject()
    useDraftStore.setState({ codeRefs: [] })
    renderWorkspace()

    expect(termsBox()).toBeNull()
  })

  it('勾了專案才出現，並說明留空的行為', () => {
    seedCodeProject()
    useDraftStore.setState({ codeRefs: [{ project_id: 3, environment: 'production' }] })
    renderWorkspace()

    expect(termsBox()).toBeInTheDocument()
    expect(screen.getByText(/留空就讓系統自己從問題裡抽/)).toBeInTheDocument()
  })

  it('**打字之後回顯切出來的關鍵字**（打成全角逗號的人才看得出來）', async () => {
    seedCodeProject()
    useDraftStore.setState({ codeRefs: [{ project_id: 3, environment: 'production' }] })
    renderWorkspace()

    await userEvent.type(termsBox()!, 'sendPush, retryCount')

    expect(useDraftStore.getState().codeTerms).toBe('sendPush, retryCount')
    expect(
      screen.getByText(/會用這 2 個關鍵字搜，取代系統自動抽的：sendPush、retryCount/),
    ).toBeInTheDocument()
  })

  it('串流中不讓改', () => {
    seedCodeProject()
    useDraftStore.setState({
      codeRefs: [{ project_id: 3, environment: 'production' }],
      streaming: true,
    })
    renderWorkspace()

    expect(termsBox()).toBeDisabled()
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

/**
 * 讀回已經存下來的草稿。
 *
 * 為什麼這一段要存在：草稿一直都寫進 `draft_replies`，但在這之前沒有任何
 * 路徑讀得回來（`repo.latest_draft()` 寫好了、零呼叫者）。於是重新整理、
 * 切回收件匣再點進來、或隔天再開，畫面都是空的——**看起來像草稿沒了，
 * 實際上它在資料庫裡**（本機實測 53 筆）。
 *
 * 兩件事一樣重要：讀得回來，以及**讀回來的那份要看得出是舊的**。後者不做
 * 的話它與剛產生的長得一模一樣，而它可能是好幾天前、用當時的設定產的，
 * 直接送出去就是送出過期的回覆。
 */
describe('讀回既有草稿', () => {
  const STORED = {
    draft_id: 80,
    mention_id: 45,
    content_md: '### ✍️ 建議回話\n這是三天前存下來的版本。',
    generation_config: {
      provider: 'claude_cli',
      model: 'claude-cli:opus',
      persona_id: 1,
      persona_name: '羅振宇（羅胖）',
      sepia: true,
      polished: true,
      polisher: 'sepia',
      polish_model: 'claude-cli:opus',
    },
    created_at: '2026-09-08T01:20:41Z',
    sent_at: null,
  }

  /** 清成「什麼都還沒有」，才看得出來是讀回來的。 */
  function seedEmptyDraft() {
    useDraftStore.setState({ raw: '', replyText: '', mentionId: null, meta: null, polish: null })
  }

  function renderWith(mention: Mention) {
    return render(
      <RouterProvider>
        <DraftReplyWorkspace mention={mention} />
      </RouterProvider>,
    )
  }

  it('**has_draft 的 Mention 一掛載就把草稿讀回來**', async () => {
    seedEmptyDraft()
    const loadStored = vi.fn(async (id: number) => {
      useDraftStore.setState({
        raw: STORED.content_md,
        replyText: STORED.content_md,
        mentionId: id,
        restored: true,
        restoredAt: STORED.created_at,
      })
      return true
    })
    useDraftStore.setState({ loadStored })

    renderWith({ ...MENTION, has_draft: true })

    await waitFor(() => expect(loadStored).toHaveBeenCalledWith(45))
    expect(await screen.findByText(/這是三天前存下來的版本/)).toBeInTheDocument()
  })

  it('正對照：沒有 has_draft 就不去讀（多數 Mention 都沒有草稿）', async () => {
    seedEmptyDraft()
    const loadStored = vi.fn(async () => false)
    useDraftStore.setState({ loadStored })

    renderWith({ ...MENTION, has_draft: false })

    await waitFor(() => expect(screen.getByText(/勾好 Reference Space/)).toBeInTheDocument())
    expect(loadStored).not.toHaveBeenCalled()
  })

  it('**還原的草稿要看得出是舊的，並說出產生時間**', () => {
    useDraftStore.setState({
      raw: STORED.content_md,
      replyText: STORED.content_md,
      mentionId: 45,
      restored: true,
      restoredAt: STORED.created_at,
    })
    renderWith(MENTION)

    expect(screen.getByText('這是先前存下來的草稿')).toBeInTheDocument()
    expect(screen.getByText(/產生於/)).toBeInTheDocument()
    // 必須明講缺了什麼——半套的證據看起來像完整的才是最危險的
    expect(screen.getByText(/脈絡證據沒有保存/)).toBeInTheDocument()
  })

  it('正對照：剛產生的草稿沒有那塊提示', () => {
    useDraftStore.setState({
      raw: STORED.content_md,
      replyText: STORED.content_md,
      mentionId: 45,
      restored: false,
      restoredAt: null,
    })
    renderWith(MENTION)

    expect(screen.queryByText('這是先前存下來的草稿')).toBeNull()
  })
})

/**
 * 還原提示那句話要跟著「證據到底全不全」走。
 *
 * 新格式（2026-09-11 起）連整份 meta 一起存，證據欄是完整的——對那種草稿
 * 還說「脈絡證據沒有保存」就是一句假話，而且會讓人白白重新產生一次、
 * 燒掉一次 AI 配額。舊的那 53 筆則相反，必須繼續講實話。
 */
describe('還原提示要說對「證據全不全」', () => {
  function renderRestored(meta: unknown) {
    useDraftStore.setState({
      raw: '### ✍️ 建議回話\n內容',
      replyText: '內容',
      mentionId: 45,
      restored: true,
      restoredAt: '2026-09-08T01:20:41Z',
      meta: meta as never,
    })
    return render(
      <RouterProvider>
        <DraftReplyWorkspace mention={MENTION} />
      </RouterProvider>,
    )
  }

  it('**有完整 meta 時不可以說「沒有保存」**', () => {
    renderRestored({
      type: 'meta',
      mention_id: 45,
      context: { mode: 'thread', message_count: 42, coverage: 'full', blocks: [] },
    })

    expect(screen.getByText('這是先前存下來的草稿')).toBeInTheDocument()
    expect(screen.getByText(/證據欄是產生當下記錄的完整內容/)).toBeInTheDocument()
    expect(screen.queryByText(/脈絡證據沒有保存/)).toBeNull()
  })

  it('舊格式（沒有 context）仍然照實說缺了什麼', () => {
    renderRestored({ type: 'meta', mention_id: 45, reply: { sepia: false } })

    expect(screen.getByText(/脈絡證據沒有保存/)).toBeInTheDocument()
    expect(screen.queryByText(/完整內容/)).toBeNull()
  })
})

/**
 * 產生完就把設定側欄收起來。
 *
 * 1440 下主區只有約 760px（導覽 56 ＋ 收件匣 288 ＋ 證據欄 320 是固定的），
 * 再扣掉 280px 的側欄，產出卡片剩不到 440px，程式碼檔名會折行。設定是
 * 「產生前」的事，看草稿時不需要它一直佔著。
 *
 * 這一組守的是**收合的時機**：自動行為不可以推翻使用者剛剛做的決定，
 * 也不可以在他沒按過產生的情況下（讀回舊草稿）突然把側欄收走。
 * 收合與否看得出來的訊號是「展開設定」這顆鈕在不在。
 */
describe('設定側欄的自動收合', () => {
  const expandButton = () => screen.queryByRole('button', { name: '展開設定' })

  /** 串流結束：streaming 由 true 翻成 false，同時帶進產出內容。 */
  function finishStreaming(raw = '### ✍️ 建議回話\n產好了') {
    act(() => {
      useDraftStore.setState({ streaming: false, raw })
    })
  }

  function renderStreaming() {
    useDraftStore.setState({ streaming: true, raw: '' })
    return renderWorkspace()
  }

  it('**串流結束且有內容時自動收合**，而且收合那一條仍講得出目前的設定', () => {
    renderStreaming()
    expect(expandButton()).toBeNull()

    finishStreaming()

    expect(expandButton()).toBeInTheDocument()
    // 收合不等於把設定藏起來：看不到摘要的人會以為自己沒設定過
    expect(screen.getByText('參考 Space 0 個')).toBeInTheDocument()
  })

  it('正對照：串流結束卻沒有產出內容（中途停掉）時不收合', () => {
    renderStreaming()

    finishStreaming('')

    expect(expandButton()).toBeNull()
  })

  it('**讀回既有草稿不會被收合突襲**——那不是使用者剛按下產生', () => {
    // seedStores 的狀態就是「有內容、沒有串流」，也就是讀回來的那一份
    renderWorkspace()

    expect(expandButton()).toBeNull()
  })

  it('**使用者手動操作過之後就不再自動收合**', async () => {
    renderStreaming()

    await userEvent.click(screen.getByRole('button', { name: '收合設定' }))
    expect(expandButton()).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '展開設定' }))
    expect(expandButton()).toBeNull()

    finishStreaming()

    // 他剛剛才親手展開，串流結束不該再把它收回去
    expect(expandButton()).toBeNull()
  })

  it('收合與展開都帶 aria-expanded', async () => {
    renderStreaming()

    const collapse = screen.getByRole('button', { name: '收合設定' })
    expect(collapse).toHaveAttribute('aria-expanded', 'true')

    await userEvent.click(collapse)

    expect(expandButton()).toHaveAttribute('aria-expanded', 'false')
  })
})
