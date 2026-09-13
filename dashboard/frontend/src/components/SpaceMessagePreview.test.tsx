import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SpaceMessagePreview } from '@/components/SpaceMessagePreview'
import { usePreviewStore } from '@/store/preview'
import type { ChatMessage, Space } from '@/lib/types'

/**
 * Space 訊息預覽（規格 §14 P4 的拆檔）。
 *
 * **這一份是特徵測試**：先寫、先跑拆檔前的程式碼確認會過，再跑拆檔後。
 * 斷言集中在這個面板存在的理由與它修過的兩個缺陷：
 *
 * 1. **同一則訊息不可以出現兩次**——最早的版本外層把整串每一則都印出來、
 *    點開又印一次。`data-message-name` 就是為了驗這件事才加的。
 * 2. **點開一串補回來的訊息要說清楚來源**——「最近 N 則」是按時間取的，
 *    常常把一串切成片段，不說的話使用者會以為那幾則本來就在清單裡。
 *
 * 則數選擇與摘要的「抓取則數」刻意分開，那條在 `store/preview` 那一側。
 */

const SPACE = { id: 'spaces/x', displayName: '工程討論' } as Space

function message(over: Partial<ChatMessage> = {}): ChatMessage {
  return {
    name: 'spaces/x/messages/1',
    sender: '陳柏元',
    sender_id: 'users/1',
    time: '2026-09-10 10:00',
    text: '這邊我確認過了',
    thread_name: null,
    attachment_note: null,
    ...over,
  } as ChatMessage
}

/** 兩則獨立訊息 ＋ 一串三則（其中兩則在視窗裡）。 */
const MESSAGES: ChatMessage[] = [
  message({ name: 'm1', text: '第一則' }),
  message({ name: 't1', text: '串內第一句', thread_name: 'spaces/x/threads/T' }),
  message({ name: 't2', text: '串內第二句', thread_name: 'spaces/x/threads/T', sender: '林小美' }),
  message({ name: 'm2', text: '第二則', sender: '王大文' }),
]

function seed(over: Record<string, unknown> = {}) {
  usePreviewStore.setState({
    spaceId: SPACE.id,
    limit: 20,
    messages: MESSAGES,
    loading: false,
    error: null,
    openThreads: [],
    expanded: {},
    expanding: null,
    collapsedOverride: null,
    // load 走 API，測試裡一律 no-op（元件在 mount 時會呼叫一次）
    load: async () => {},
    ...over,
  })
}

beforeEach(() => {
  seed()
})

describe('SpaceMessagePreview', () => {
  it('沒選 Space 就什麼都不畫', () => {
    const { container } = render(<SpaceMessagePreview space={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('列出視窗內的訊息，討論串在外層只佔一列', () => {
    render(<SpaceMessagePreview space={SPACE} />)

    expect(screen.getByText('第一則')).toBeInTheDocument()
    expect(screen.getByText('第二則')).toBeInTheDocument()
    // 串內兩則在外層收合成一列，顯示則數與參與者
    expect(screen.getByText('討論串 2 則')).toBeInTheDocument()
    expect(screen.getByText('陳柏元、林小美')).toBeInTheDocument()
  })

  it('**同一則訊息不會出現兩次**（這個面板最早的缺陷）', async () => {
    render(<SpaceMessagePreview space={SPACE} />)

    const countOf = (name: string) =>
      document.querySelectorAll(`[data-message-name="${name}"]`).length

    // 收合狀態：串內兩則都不該以獨立訊息的身分出現
    expect(countOf('t1')).toBe(0)
    expect(countOf('t2')).toBe(0)

    await userEvent.click(screen.getByRole('button', { name: /討論串 2 則/ }))
    // 展開之後各出現一次，不是兩次
    expect(countOf('t1')).toBe(1)
    expect(countOf('t2')).toBe(1)
    // 正對照：獨立訊息從頭到尾都是一次，證明這個計數真的數得到東西
    expect(countOf('m1')).toBe(1)
  })

  it('**點開時補回來的訊息要說清楚它們原本不在清單裡**', async () => {
    // 整串其實有三則，視窗裡只看到兩則
    seed({
      openThreads: ['spaces/x/threads/T'],
      expanded: {
        'spaces/x/threads/T': [
          message({ name: 't0', text: '串的開頭', thread_name: 'spaces/x/threads/T' }),
          message({ name: 't1', text: '串內第一句', thread_name: 'spaces/x/threads/T' }),
          message({ name: 't2', text: '串內第二句', thread_name: 'spaces/x/threads/T' }),
        ],
      },
    })
    render(<SpaceMessagePreview space={SPACE} />)

    expect(screen.getByText(/其中 1 則原本不在上面的清單範圍內/)).toBeInTheDocument()
  })

  it('（正對照）整串沒有多出來時不講那句話', () => {
    seed({
      openThreads: ['spaces/x/threads/T'],
      expanded: {
        'spaces/x/threads/T': [
          message({ name: 't1', text: '串內第一句', thread_name: 'spaces/x/threads/T' }),
          message({ name: 't2', text: '串內第二句', thread_name: 'spaces/x/threads/T' }),
        ],
      },
    })
    render(<SpaceMessagePreview space={SPACE} />)

    expect(screen.queryByText(/原本不在上面的清單範圍內/)).toBeNull()
  })

  it('只有圖沒有文字的訊息也看得見（附件說明）', () => {
    seed({
      messages: [message({ name: 'img', text: '', attachment_note: '[圖片：shot.png（AI 未讀取內容）]' })],
    })
    render(<SpaceMessagePreview space={SPACE} />)

    expect(screen.getByText('[圖片：shot.png（AI 未讀取內容）]')).toBeInTheDocument()
  })

  it('收起來之後看不到訊息，展開鈕的 aria-expanded 跟著變', async () => {
    render(<SpaceMessagePreview space={SPACE} />)
    const toggle = screen.getByRole('button', { name: /最近訊息/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')

    await userEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('第一則')).toBeNull()
  })

  it('defaultCollapsed 只是預設，使用者按過就聽他的', async () => {
    render(<SpaceMessagePreview space={SPACE} defaultCollapsed />)
    expect(screen.queryByText('第一則')).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: /最近訊息/ }))
    expect(screen.getByText('第一則')).toBeInTheDocument()
  })

  it('三個則數按鈕，目前那個用 aria-pressed 標示', () => {
    render(<SpaceMessagePreview space={SPACE} />)
    const group = screen.getByRole('group', { name: '預覽則數' })
    const pressed = within(group)
      .getAllByRole('button')
      .filter((b) => b.getAttribute('aria-pressed') === 'true')
    expect(within(group).getAllByRole('button')).toHaveLength(3)
    expect(pressed).toHaveLength(1)
    expect(pressed[0]).toHaveTextContent('20')
  })

  it('**說出「訊息不會存進資料庫」**（原本只活在 tooltip 裡）', () => {
    render(<SpaceMessagePreview space={SPACE} />)
    expect(screen.getByText(/不會存進資料庫/)).toBeInTheDocument()
  })

  it('讀不到訊息與錯誤各有各的說法', () => {
    seed({ messages: [], error: null })
    const { unmount } = render(<SpaceMessagePreview space={SPACE} />)
    expect(screen.getByText('這個 Space 讀不到任何訊息。')).toBeInTheDocument()
    unmount()

    seed({ messages: [], error: '拿不到訊息：權限不足' })
    render(<SpaceMessagePreview space={SPACE} />)
    expect(screen.getByText('拿不到訊息：權限不足')).toBeInTheDocument()
  })

  it('重新讀取會強制重打一次', async () => {
    const load = vi.fn(async () => {})
    seed({ load })
    render(<SpaceMessagePreview space={SPACE} />)
    load.mockClear()

    await userEvent.click(screen.getByRole('button', { name: /重新讀取/ }))
    expect(load).toHaveBeenCalledWith(SPACE.id, { force: true })
  })

  it('**換 Space 的瞬間不拿別人的訊息充數**', () => {
    // store 裡還是舊 Space 的資料
    seed({ spaceId: 'spaces/old' })
    render(<SpaceMessagePreview space={SPACE} />)
    expect(screen.queryByText('第一則')).toBeNull()
    expect(screen.getByText('這個 Space 讀不到任何訊息。')).toBeInTheDocument()
  })

  it('**渲染結果裡 [title] 選得到 0 個**（規格 §10.3）', () => {
    const { container } = render(<SpaceMessagePreview space={SPACE} />)
    expect(container.querySelectorAll('[title]')).toHaveLength(0)
  })
})
