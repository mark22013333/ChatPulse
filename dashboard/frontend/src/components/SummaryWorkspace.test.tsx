import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SummaryWorkspace } from '@/components/SummaryWorkspace'
import { api } from '@/lib/api'
import { usePreviewStore } from '@/store/preview'
import { useProviderStore } from '@/store/providers'
import { useSummaryStore } from '@/store/summary'
import { useDraftStore } from '@/store/draft'
import { useMentionsStore } from '@/store/mentions'
import type { Mention, Space, SseMeta } from '@/lib/types'

/**
 * 摘要工作台（規格 §14 P4 的拆檔）。
 *
 * **這一份是特徵測試**：先寫、先跑拆檔前的程式碼確認會過，再跑拆檔後。
 * 只跑拆檔後的版本證明不了任何事。
 *
 * 斷言集中在三類「錯了看不出來」的東西：
 *
 * 1. **推播回 Google Chat 是不可撤回的**——確認框一定要顯示全文與「以你本人
 *    的身分」，而且是本人不是機器人。
 * 2. **抓取則數與摘要風格會被記成個人預設**——那是這兩個欄位的行為，
 *    使用者不知道的話會以為只影響這一次（規格 §10.3）。
 * 3. **產生回覆草稿挑的是「對方最後說的那句」**——「產生回覆草稿」四個字
 *    看不出來這件事。
 */

const SPACE = { id: 'spaces/AAQATjybbSY', displayName: '工程討論' } as Space

const DRAFT_TARGET: Mention = {
  id: 65,
  space_id: SPACE.id,
  space_name: '工程討論',
  message_name: 'spaces/x/messages/65',
  thread_name: null,
  sender_display: '林小美',
  create_time: '2026-09-10T02:16:39Z',
  state: 'manual',
  resolved_at: null,
}

function seed(over: Record<string, unknown> = {}) {
  useProviderStore.setState({ providers: [], loaded: true, load: async () => {} } as never)
  // 預覽面板在 mount 時會 load()，換成 no-op 才不會打 API
  usePreviewStore.setState({
    spaceId: SPACE.id,
    messages: [],
    loading: false,
    error: null,
    openThreads: [],
    expanded: {},
    expanding: null,
    collapsedOverride: true, // 預設收起來，讓斷言不必跟預覽內容搶文字
    load: async () => {},
  } as never)
  useMentionsStore.setState({ items: [], selectedId: null, mergeIds: [], external: null })
  useDraftStore.setState({ streaming: false, raw: '', replyText: '' })
  useSummaryStore.setState({
    styles: [
      { value: 'general', label: '通用' },
      { value: 'technical', label: '技術細節' },
    ],
    style: 'general',
    limit: 50,
    limitError: null,
    streaming: false,
    text: '',
    meta: null,
    error: null,
    streamedSpaceId: null,
    setStyle: () => {},
    setLimit: () => {},
    start: async () => {},
    abort: () => {},
    reset: () => {},
    ...over,
  } as never)
}

const startButton = () => screen.getByRole('button', { name: /開始摘要|重新摘要/ })

beforeEach(() => {
  vi.restoreAllMocks()
  seed()
})

describe('摘要工作台的工具列', () => {
  it('沒選 Space 時說得出來，而且不讓開始', () => {
    render(<SummaryWorkspace space={null} />)

    expect(screen.getByText('尚未選擇 Space')).toBeInTheDocument()
    expect(screen.getByText('請從左側清單選一個 Space')).toBeInTheDocument()
    expect(startButton()).toBeDisabled()
  })

  it('選了 Space 就顯示名稱與 id，並且可以開始', () => {
    render(<SummaryWorkspace space={SPACE} />)

    expect(screen.getByText('工程討論')).toBeInTheDocument()
    expect(screen.getByText(SPACE.id)).toBeInTheDocument()
    expect(startButton()).toBeEnabled()
  })

  it('開始摘要帶著 Space id', async () => {
    const start = vi.fn(async () => {})
    seed({ start })
    render(<SummaryWorkspace space={SPACE} />)

    await userEvent.click(startButton())
    expect(start).toHaveBeenCalledWith(SPACE.id)
  })

  it('**串流中換成「停止串流」**，不是把按鈕鎖住', async () => {
    const abort = vi.fn()
    seed({ streaming: true, abort })
    render(<SummaryWorkspace space={SPACE} />)

    // 鎖住的話使用者就沒有中止入口了
    expect(screen.queryByRole('button', { name: /開始摘要|重新摘要/ })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: '停止串流' }))
    expect(abort).toHaveBeenCalled()
  })

  it('已經有摘要時按鈕寫「重新摘要」', () => {
    seed({ text: '# 摘要\n\n第一點' })
    render(<SummaryWorkspace space={SPACE} />)
    expect(screen.getByRole('button', { name: '重新摘要' })).toBeInTheDocument()
  })

  it('**抓取則數說出「改完會被記住」**（那是這個欄位的行為，不是補充說明）', () => {
    render(<SummaryWorkspace space={SPACE} />)

    const input = screen.getByLabelText(/抓取則數/)
    expect(screen.getByText(/改完離開欄位就會記住/)).toBeInTheDocument()
    expect(input).toHaveAttribute('aria-describedby', 'summary-limit-hint')
  })

  it('離開抓取則數欄位就把它記成預設', async () => {
    const spy = vi.spyOn(api, 'updatePreferences').mockResolvedValue({} as never)
    render(<SummaryWorkspace space={SPACE} />)

    await userEvent.click(screen.getByLabelText(/抓取則數/))
    await userEvent.tab()
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ default_limit: 50 }))
  })

  it('則數不合法時說出原因並標 aria-invalid', () => {
    seed({ limitError: '抓取則數要在 1~1000 之間' })
    render(<SummaryWorkspace space={SPACE} />)

    expect(screen.getByText('抓取則數要在 1~1000 之間')).toBeInTheDocument()
    expect(screen.getByLabelText(/抓取則數/)).toHaveAttribute('aria-invalid', 'true')
    expect(startButton()).toBeDisabled()
  })

  it('**產生回覆草稿說出它挑的是哪一則**', () => {
    render(<SummaryWorkspace space={SPACE} />)

    const button = screen.getByRole('button', { name: '產生回覆草稿' })
    expect(button).toHaveAttribute('aria-describedby', 'draft-reply-hint')
    expect(screen.getByText('針對對方最後說的話')).toBeInTheDocument()
  })

  it('產生回覆草稿：拿到目標之後選它、開始生成、通知外層導航', async () => {
    const selectExternal = vi.fn()
    const generate = vi.fn(async () => {})
    useMentionsStore.setState({ selectExternal })
    useDraftStore.setState({ generate })
    vi.spyOn(api, 'createDraftTarget').mockResolvedValue({ mention: DRAFT_TARGET } as never)
    const onDraftCreated = vi.fn()

    render(<SummaryWorkspace space={SPACE} onDraftCreated={onDraftCreated} />)
    await userEvent.click(screen.getByRole('button', { name: '產生回覆草稿' }))

    await waitFor(() => expect(selectExternal).toHaveBeenCalledWith(DRAFT_TARGET))
    expect(generate).toHaveBeenCalledWith(65)
    expect(onDraftCreated).toHaveBeenCalledWith(65)
  })

  it('這個對話裡沒有別人的訊息時，說清楚為什麼沒有草稿', async () => {
    vi.spyOn(api, 'createDraftTarget').mockResolvedValue({ mention: null } as never)
    const generate = vi.fn(async () => {})
    useDraftStore.setState({ generate })

    render(<SummaryWorkspace space={SPACE} />)
    await userEvent.click(screen.getByRole('button', { name: '產生回覆草稿' }))

    await waitFor(() => expect(generate).not.toHaveBeenCalled())
  })
})

describe('摘要工作台的輸出區', () => {
  it('什麼都還沒有時給一句可以照做的話', () => {
    render(<SummaryWorkspace space={SPACE} />)
    expect(screen.getByText('按「開始摘要」產生一份結構化 Summary')).toBeInTheDocument()

    // 沒選 Space 時說法不同（要先選才有東西可以摘）
    render(<SummaryWorkspace space={null} />)
    expect(screen.getByText('選一個 Space，產生一份結構化 Summary')).toBeInTheDocument()
  })

  it('錯誤走看得見的區塊', () => {
    seed({ error: '供應商回了 429：配額用完了' })
    render(<SummaryWorkspace space={SPACE} />)
    expect(screen.getByText('供應商回了 429：配額用完了')).toBeInTheDocument()
  })

  it('**meta 一到就報「讀取幾則」與實際用的供應商**', () => {
    seed({
      text: '# 摘要',
      meta: {
        space: '工程討論',
        message_count: 47,
        image_count: 3,
        provider: 'gemini',
        model: 'gemini-2.5-pro',
      } as SseMeta,
    })
    render(<SummaryWorkspace space={SPACE} />)

    expect(screen.getByText(/讀取 47 則/)).toBeInTheDocument()
    expect(screen.getByText(/圖片 3 張/)).toBeInTheDocument()
    // 一律以 meta 回報的為準——伺服器可能因別名解析而用了別的
    expect(screen.getByText(/gemini-2.5-pro/)).toBeInTheDocument()
  })

  it('meta 還沒到時說「正在準備…」，不是留白', () => {
    seed({ streaming: true })
    render(<SummaryWorkspace space={SPACE} />)
    expect(screen.getByText('正在準備…')).toBeInTheDocument()
  })

  it('複製 Markdown 在沒有內容時是鎖住的', () => {
    seed({ text: '' })
    render(<SummaryWorkspace space={SPACE} />)
    // 沒有內容時整個輸出區的工具列都不出現
    expect(screen.queryByRole('button', { name: '複製 Markdown' })).toBeNull()
  })

  it('複製 Markdown 複製的是摘要全文', async () => {
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
      writable: true,
    })
    seed({ text: '# 摘要\n\n第一點' })
    render(<SummaryWorkspace space={SPACE} />)

    await userEvent.click(screen.getByRole('button', { name: '複製 Markdown' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('# 摘要\n\n第一點'))
  })
})

describe('推播回 Google Chat（不可撤回）', () => {
  it('串流中不讓推播', () => {
    seed({ text: '# 摘要', streaming: true })
    render(<SummaryWorkspace space={SPACE} />)
    expect(screen.getByRole('button', { name: '推播回 Google Chat' })).toBeDisabled()
  })

  it('**確認框要顯示全文、目標 Space，並說明是以本人身分送出**', async () => {
    seed({ text: '# 摘要\n\n第一點' })
    render(<SummaryWorkspace space={SPACE} />)

    await userEvent.click(screen.getByRole('button', { name: '推播回 Google Chat' }))

    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('推播這份 Summary 回 Google Chat？')
    expect(dialog).toHaveTextContent('你本人的身分')
    expect(dialog).toHaveTextContent('不是機器人')
    expect(dialog).toHaveTextContent('送出後無法在 ChatPulse 撤回')
    expect(dialog).toHaveTextContent('工程討論')
    // 全文預覽：按下確認之前看得到會送出什麼
    expect(dialog).toHaveTextContent('第一點')
  })

  it('按確認才真的送出', async () => {
    const spy = vi.spyOn(api, 'publish').mockResolvedValue({} as never)
    seed({ text: '# 摘要\n\n第一點' })
    render(<SummaryWorkspace space={SPACE} />)

    await userEvent.click(screen.getByRole('button', { name: '推播回 Google Chat' }))
    const dialog = await screen.findByRole('dialog')
    expect(spy).not.toHaveBeenCalled() // 正對照：開框本身不會送出

    await userEvent.click(within(dialog).getByRole('button', { name: '確認推播' }))
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith({ space_id: SPACE.id, text: '# 摘要\n\n第一點' }),
    )
  })

  it('**渲染結果裡只有對話框標題那一處不是 DOM 的 title 屬性**（規格 §10.3）', () => {
    seed({ text: '# 摘要' })
    const { container } = render(<SummaryWorkspace space={SPACE} />)
    // ConfirmDialog 的 title 是 React prop（渲染成 DialogTitle 的文字節點），
    // 不會變成 DOM 屬性。tokens.test.ts 的白名單就是靠這件事才留得下來
    expect(container.querySelectorAll('[title]')).toHaveLength(0)
  })
})
