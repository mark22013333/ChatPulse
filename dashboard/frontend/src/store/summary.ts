import { create } from 'zustand'
import { api, errorMessage, LIMIT_DEFAULT, streamUrls } from '@/lib/api'
import { streamSse } from '@/lib/sse'
import type { SseMeta, SummaryRecord, SummaryStyle, SummaryStyleValue } from '@/lib/types'

interface SummaryState {
  styles: SummaryStyle[]
  style: SummaryStyleValue
  limit: number
  /** limit 前端驗證訊息（1~1000），有值時禁止送出 */
  limitError: string | null

  streaming: boolean
  text: string
  meta: SseMeta | null
  summaryId: number | null
  error: string | null
  /** 這次串流是針對哪個 Space（避免切換後畫面錯亂） */
  streamedSpaceId: string | null

  history: SummaryRecord[]
  historyLoading: boolean

  loadStyles: () => Promise<void>
  loadHistory: () => Promise<void>
  setStyle: (style: SummaryStyleValue) => void
  setLimit: (raw: string) => void
  applyDefaults: (defaults: { limit?: number; style?: SummaryStyleValue }) => void
  start: (spaceId: string) => Promise<void>
  abort: () => void
  reset: () => void
}

let controller: AbortController | null = null

export const useSummaryStore = create<SummaryState>((set, get) => ({
  styles: [],
  style: 'general',
  limit: LIMIT_DEFAULT,
  limitError: null,

  streaming: false,
  text: '',
  meta: null,
  summaryId: null,
  error: null,
  streamedSpaceId: null,

  history: [],
  historyLoading: false,

  loadStyles: async () => {
    try {
      const data = await api.styles()
      if (data.styles?.length) set({ styles: data.styles })
    } catch {
      // 拿不到風格清單就退回三個內建選項，不阻擋主流程
      set({
        styles: [
          { value: 'general', label: '通用' },
          { value: 'technical', label: '技術細節' },
          { value: 'action_only', label: '只要待辦' },
        ],
      })
    }
  },

  loadHistory: async () => {
    set({ historyLoading: true })
    try {
      const data = await api.summaries(50)
      set({ history: data.summaries ?? [] })
    } catch {
      // 歷史清單失敗不影響主流程
    } finally {
      set({ historyLoading: false })
    }
  },

  setStyle: (style) => set({ style }),

  setLimit: (raw) => {
    const trimmed = raw.trim()
    if (trimmed === '') {
      set({ limit: Number.NaN, limitError: '請輸入抓取則數' })
      return
    }
    const parsed = Number(trimmed)
    if (!Number.isInteger(parsed)) {
      set({ limit: Number.NaN, limitError: '抓取則數必須是整數' })
      return
    }
    if (parsed < 1 || parsed > 1000) {
      set({ limit: parsed, limitError: '抓取則數必須介於 1 至 1000 之間' })
      return
    }
    set({ limit: parsed, limitError: null })
  },

  applyDefaults: ({ limit, style }) => {
    set((state) => ({
      limit: limit && limit >= 1 && limit <= 1000 ? limit : state.limit,
      style: style ?? state.style,
    }))
  },

  start: async (spaceId) => {
    const { limit, style, limitError } = get()
    if (limitError || !Number.isInteger(limit)) return

    controller?.abort()
    controller = new AbortController()
    const signal = controller.signal

    set({
      streaming: true,
      text: '',
      meta: null,
      summaryId: null,
      error: null,
      streamedSpaceId: spaceId,
    })

    await streamSse(
      streamUrls.summarize(),
      { space_id: spaceId, limit, style },
      {
        onMeta: (meta) => set({ meta }),
        onChunk: (chunk) => set((state) => ({ text: state.text + (chunk.text ?? '') })),
        onDone: (done) => {
          set({ streaming: false, summaryId: done.summary_id ?? null })
          void get().loadHistory()
        },
        onError: (event) => set({ streaming: false, error: `${event.message}（${event.code}）` }),
      },
      signal,
    ).catch((err) => {
      set({ streaming: false, error: errorMessage(err) })
    })

    if (get().streaming) set({ streaming: false })
  },

  abort: () => {
    controller?.abort()
    controller = null
    set({ streaming: false })
  },

  reset: () => {
    controller?.abort()
    controller = null
    set({ streaming: false, text: '', meta: null, summaryId: null, error: null, streamedSpaceId: null })
  },
}))
