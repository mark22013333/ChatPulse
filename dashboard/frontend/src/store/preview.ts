import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import type { ChatMessage } from '@/lib/types'

/** 預覽可選的則數。刻意不跟摘要的「抓取則數」共用（見 PREVIEW_LIMITS 下方說明）。 */
export const PREVIEW_LIMITS = [10, 20, 30] as const
export type PreviewLimit = (typeof PREVIEW_LIMITS)[number]

/**
 * Space 訊息預覽：點一個 Space 就看得到最近幾則在講什麼。
 *
 * **為什麼不與摘要的「抓取則數」共用同一個數字**：那個是「要摘多少東西」
 * （產出涵蓋範圍，預設 50、上限 1000），這個是「我想先瞄幾則」。
 * 共用的話，把預覽調成 10 會讓下一次摘要只摘 10 則，而且 UI 上看不出來
 * ——同一個旋鈕控制兩件性質不同的事，使用者調整其一必然誤調另一。
 *
 * 狀態放 store 而不是元件：頁籤是條件渲染，切走再切回來會整個重掛，
 * 放元件 useState 的話每次切頁籤都要重打一次 API。
 */
interface PreviewState {
  /** 目前這份預覽是哪個 Space 的 */
  spaceId: string | null
  limit: PreviewLimit
  messages: ChatMessage[]
  loading: boolean
  error: string | null
  /** 使用者按過「展開整串」的討論串，thread_name -> 整串訊息 */
  expanded: Record<string, ChatMessage[]>
  expanding: string | null
  /**
   * 收合狀態。`null` ＝使用者沒表示過意見，跟隨畫面的預設
   * （有摘要時收起來讓摘要當主角）；一旦他自己按過，就一直聽他的。
   * 用一個布林值的話，「預設」與「使用者的選擇」會互相蓋來蓋去。
   */
  collapsedOverride: boolean | null

  load: (spaceId: string, options?: { force?: boolean }) => Promise<void>
  setLimit: (limit: PreviewLimit) => void
  expandThread: (spaceId: string, threadName: string) => Promise<void>
  collapseThread: (threadName: string) => void
  setCollapsed: (value: boolean) => void
  reset: () => void
}

export const usePreviewStore = create<PreviewState>((set, get) => ({
  spaceId: null,
  limit: 20,
  messages: [],
  loading: false,
  error: null,
  expanded: {},
  expanding: null,
  collapsedOverride: null,

  load: async (spaceId, options = {}) => {
    const { spaceId: current, limit, loading } = get()
    // 同一個 Space、已經載過就不重打（切頁籤回來不該再燒一次 API）
    if (!options.force && current === spaceId && get().messages.length > 0) return
    // 載入中只擋「同一個 Space 的重複請求」。**不可以連換 Space 也一起擋**——
    // A 還在載入時點 B，B 會被吃掉，而 useEffect 不會再觸發一次，
    // 預覽就永遠空在那裡，畫面上沒有任何錯誤可看。晚到的舊回應由下方的
    // 「spaceId 已經變了就丟棄」擋掉，不需要靠這裡。
    if (loading && current === spaceId) return

    // 換 Space 就把展開過的討論串清掉（那些是上一個 Space 的），
    // 收合的選擇也一併重置——他對上一個 Space 收起來，不代表對這個也要
    set({
      spaceId,
      loading: true,
      error: null,
      ...(current !== spaceId
        ? { messages: [], expanded: {}, collapsedOverride: null }
        : {}),
    })
    try {
      const data = await api.messages({ space_id: spaceId, limit })
      // 期間使用者可能已經換了 Space，晚到的回應不要蓋掉新的
      if (get().spaceId !== spaceId) return
      set({ messages: data.messages ?? [] })
    } catch (err) {
      if (get().spaceId !== spaceId) return
      set({ error: errorMessage(err), messages: [] })
    } finally {
      if (get().spaceId === spaceId) set({ loading: false })
    }
  },

  setLimit: (limit) => {
    set({ limit })
    const { spaceId } = get()
    if (spaceId) void get().load(spaceId, { force: true })
  },

  expandThread: async (spaceId, threadName) => {
    if (get().expanded[threadName]) return
    set({ expanding: threadName })
    try {
      const data = await api.messages({
        space_id: spaceId,
        thread_name: threadName,
        limit: 200,
      })
      set((state) => ({ expanded: { ...state.expanded, [threadName]: data.messages ?? [] } }))
    } catch (err) {
      set({ error: errorMessage(err) })
    } finally {
      set({ expanding: null })
    }
  },

  collapseThread: (threadName) =>
    set((state) => {
      const next = { ...state.expanded }
      delete next[threadName]
      return { expanded: next }
    }),

  setCollapsed: (value) => set({ collapsedOverride: value }),

  reset: () =>
    set({
      spaceId: null,
      messages: [],
      error: null,
      expanded: {},
      expanding: null,
      collapsedOverride: null,
    }),
}))

export interface ThreadGroup {
  threadName: string
  /** 這一串在目前這個視窗裡有幾則 */
  countInWindow: number
  /** 第幾串（1 起算），用來給人看的「討論串 1」 */
  index: number
}

/**
 * 標出哪些訊息屬於「在這個視窗裡不只一則」的討論串。
 *
 * 為什麼只標多則的：私訊幾乎每則訊息各自成一個 thread（Google Chat 的行為），
 * 全部都標的話整個清單都是徽章，等於沒標。只有真的看得出「這是一串對話」
 * 的才值得標出來。
 */
export function threadGroups(messages: ChatMessage[]): Map<string, ThreadGroup> {
  const counts = new Map<string, number>()
  for (const m of messages) {
    if (!m.thread_name) continue
    counts.set(m.thread_name, (counts.get(m.thread_name) ?? 0) + 1)
  }
  const out = new Map<string, ThreadGroup>()
  let index = 0
  // 依照第一次出現的順序編號，讀起來才跟畫面由上而下一致
  for (const m of messages) {
    const name = m.thread_name
    if (!name || out.has(name)) continue
    const countInWindow = counts.get(name) ?? 0
    if (countInWindow < 2) continue
    index += 1
    out.set(name, { threadName: name, countInWindow, index })
  }
  return out
}
