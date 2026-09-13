import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useStreamAnnouncer } from './useStreamAnnouncer'
import { Markdown } from '@/components/Markdown'
import { PROGRESS_INTERVAL_MS } from '@/lib/streamAnnouncements'
import { useDraftStore } from '@/store/draft'
import { useSummaryStore } from '@/store/summary'

/**
 * 串流宣告的接線（設計規格 §10.6 的狀態層）。
 *
 * 純函式那一層有 `lib/streamAnnouncements.test.ts` 的 22 項；這裡只驗
 * **接線**：狀態機轉換有沒有真的被偵測到、有沒有寫進對的 live region、
 * 以及最容易寫錯的那件事——**live region 必須常駐**。
 */

/** 照 AppShell 的方式掛兩個 region。 */
function Harness() {
  const { polite, alert } = useStreamAnnouncer()
  return (
    <>
      <div role="status" aria-live="polite" className="sr-only">
        {polite}
      </div>
      <div role="alert" className="sr-only">
        {alert}
      </div>
    </>
  )
}

const politeRegion = () => screen.getByRole('status')
const alertRegion = () => screen.getByRole('alert')

function resetStores() {
  useSummaryStore.setState({ streaming: false, text: '', error: null })
  useDraftStore.setState({ streaming: false, raw: '', replyText: '', error: null, polish: null })
}

beforeEach(() => {
  resetStores()
})

describe('live region 的存在本身', () => {
  it('**兩個 region 一開始就在，即使還沒有任何訊息**', () => {
    // live region 必須在內容寫進去之前就存在於無障礙樹裡，否則多數螢幕
    // 閱讀器不會念。「有訊息才渲染」是這個機制最常見的壞法。
    render(<Harness />)

    expect(politeRegion()).toBeInTheDocument()
    expect(alertRegion()).toBeInTheDocument()
    expect(politeRegion()).toHaveTextContent('')
  })
})

describe('狀態機轉換 → polite region', () => {
  it('草稿開始串流', () => {
    render(<Harness />)

    act(() => useDraftStore.setState({ streaming: true, raw: '' }))

    expect(politeRegion()).toHaveTextContent('開始產生回覆草稿')
  })

  it('**脈絡分析完成那一刻**（建議回話的標題串流出來）', () => {
    render(<Harness />)
    act(() => useDraftStore.setState({ streaming: true, raw: '### 🧭 脈絡分析\n讀完了' }))
    expect(politeRegion()).toHaveTextContent('開始產生回覆草稿')

    act(() =>
      useDraftStore.setState({ streaming: true, raw: '### 🧭 脈絡分析\n讀完了\n### ✍️ 建議回話\n' }),
    )

    expect(politeRegion()).toHaveTextContent('脈絡分析完成，開始寫建議回話')
  })

  it('**完成時報字數與 Sepia 結果**', () => {
    render(<Harness />)
    act(() =>
      useDraftStore.setState({ streaming: true, raw: '### ✍️ 建議回話\n這邊我確認過了' }),
    )

    act(() =>
      useDraftStore.setState({
        streaming: false,
        replyText: '這邊我確認過了。',
        polish: { polished: false, fallback_reason: '數字被改動' } as never,
      }),
    )

    expect(politeRegion()).toHaveTextContent('草稿完成，約 8 字')
    expect(politeRegion()).toHaveTextContent('Sepia 未採用：數字被改動')
    expect(politeRegion()).toHaveTextContent('內容在主要內容區')
  })

  it('摘要走自己的一條線（說法不同、不提 Sepia）', () => {
    render(<Harness />)

    act(() => useSummaryStore.setState({ streaming: true, text: '' }))
    expect(politeRegion()).toHaveTextContent('開始整理摘要')

    act(() => useSummaryStore.setState({ streaming: false, text: '一二三四五' }))
    expect(politeRegion()).toHaveTextContent('摘要完成，約 5 字')
    expect(politeRegion()).not.toHaveTextContent('Sepia')
  })

  it('按停止串流、什麼都沒吐出來時不宣告「完成」', () => {
    render(<Harness />)
    act(() => useDraftStore.setState({ streaming: true, raw: '' }))

    act(() => useDraftStore.setState({ streaming: false, raw: '', replyText: '' }))

    // 還是停在「開始」那一句，不會謊報完成
    expect(politeRegion()).toHaveTextContent('開始產生回覆草稿')
    expect(politeRegion()).not.toHaveTextContent('完成，約')
  })
})

describe('錯誤 → alert region', () => {
  it('**錯誤寫進 alert，不是 polite**（只有它值得打斷）', () => {
    render(<Harness />)
    act(() => useDraftStore.setState({ streaming: true, raw: '' }))

    act(() => useDraftStore.setState({ streaming: false, error: 'AI 供應商沒有回應' }))

    expect(alertRegion()).toHaveTextContent('草稿產生失敗：AI 供應商沒有回應')
    // 正對照：polite 那一邊還是上一句里程碑，錯誤沒有跑錯地方
    expect(politeRegion()).toHaveTextContent('開始產生回覆草稿')
    expect(politeRegion()).not.toHaveTextContent('失敗')
  })
})

describe('節流層：每 10 秒一次，不隨 chunk', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('**chunk 一直進來也不會多念**', () => {
    render(<Harness />)
    act(() => useDraftStore.setState({ streaming: true, raw: '起' }))
    const afterStart = politeRegion().textContent

    // 模擬十個 chunk。每個 chunk 都念一次的話，等於什麼都聽不到
    act(() => {
      for (let i = 0; i < 10; i += 1) {
        useDraftStore.setState({ raw: '起' + '字'.repeat(i + 1) })
      }
    })

    expect(politeRegion().textContent).toBe(afterStart)
  })

  it('過了 10 秒才報一次進度', () => {
    render(<Harness />)
    act(() => useDraftStore.setState({ streaming: true, raw: '一二三' }))

    act(() => vi.advanceTimersByTime(PROGRESS_INTERVAL_MS))

    expect(politeRegion()).toHaveTextContent('草稿還在產生')
    expect(politeRegion()).toHaveTextContent('目前 3 字')
  })

  it('串流停了就不再報（不會在完成之後繼續念進度）', () => {
    render(<Harness />)
    act(() => useDraftStore.setState({ streaming: true, raw: '一二三' }))
    act(() => useDraftStore.setState({ streaming: false, replyText: '一二三' }))
    const afterDone = politeRegion().textContent

    act(() => vi.advanceTimersByTime(PROGRESS_INTERVAL_MS * 3))

    expect(politeRegion().textContent).toBe(afterDone)
  })
})

describe('內容層：Markdown 絕對不可以有 aria-live', () => {
  it('**只有 aria-busy，沒有 aria-live**', () => {
    // 每個 chunk 都重寫 innerHTML，設了 aria-live 等於整段重念、
    // 念到一半又被下一個 chunk 打斷——比完全不宣告更糟
    const { container } = render(<Markdown source="# 標題" typing />)
    const body = container.querySelector('.markdown-body')

    expect(body).toHaveAttribute('aria-busy', 'true')
    expect(body).not.toHaveAttribute('aria-live')
  })

  it('沒在串流時不設 aria-busy（不是設成 false）', () => {
    const { container } = render(<Markdown source="# 標題" />)
    const body = container.querySelector('.markdown-body')

    expect(body).not.toHaveAttribute('aria-busy')
    expect(body).not.toHaveAttribute('aria-live')
  })
})
