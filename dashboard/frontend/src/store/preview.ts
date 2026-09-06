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
  /** 目前打開的討論串（收合列被點開）。與 `expanded` 分開：一個是「要不要顯示」，
   *  另一個是「整串抓回來了沒」——抓回來之前先用視窗裡已有的那幾則頂著。 */
  openThreads: string[]
  /** 已經抓回整串的快取，thread_name -> 整串訊息 */
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
  /** 點開／收起一串。第一次點開會順便去抓完整的那一串。 */
  toggleThread: (spaceId: string, threadName: string) => void
  setCollapsed: (value: boolean) => void
  reset: () => void
}

export const usePreviewStore = create<PreviewState>((set, get) => ({
  spaceId: null,
  limit: 20,
  messages: [],
  loading: false,
  error: null,
  openThreads: [],
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
        ? { messages: [], expanded: {}, openThreads: [], collapsedOverride: null }
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

  toggleThread: (spaceId, threadName) => {
    const open = get().openThreads.includes(threadName)
    if (open) {
      set((state) => ({ openThreads: state.openThreads.filter((t) => t !== threadName) }))
      return
    }
    // 先打開，畫面立刻用視窗裡已有的那幾則顯示，不要等 API 才有反應
    set((state) => ({ openThreads: [...state.openThreads, threadName] }))
    if (get().expanded[threadName] || get().expanding === threadName) return

    // 補齊被 limit 切掉的部分。只在點開時抓——一個視窗可能有二十幾串，
    // 預先全抓是二十幾次往返。
    set({ expanding: threadName })
    void api
      .messages({ space_id: spaceId, thread_name: threadName, limit: 200 })
      .then((data) => {
        set((state) => ({ expanded: { ...state.expanded, [threadName]: data.messages ?? [] } }))
      })
      .catch((err) => {
        // 抓不到整串不是致命的——視窗裡那幾則還在，照樣看得到東西
        set({ error: errorMessage(err) })
      })
      .finally(() => {
        if (get().expanding === threadName) set({ expanding: null })
      })
  },

  setCollapsed: (value) => set({ collapsedOverride: value }),

  reset: () =>
    set({
      spaceId: null,
      messages: [],
      error: null,
      openThreads: [],
      expanded: {},
      expanding: null,
      collapsedOverride: null,
    }),
}))

/** 清單上的一列：一則獨立訊息，或一整串收合成的一列。 */
export type PreviewItem =
  | { kind: 'message'; key: string; message: ChatMessage }
  | {
      kind: 'thread'
      key: string
      threadName: string
      /** 第幾串（1 起算），用來配顏色與「討論串 N」 */
      index: number
      /** 這一串**在目前這個視窗裡**的訊息（點開前先顯示這些，不必等 API） */
      messages: ChatMessage[]
    }

/**
 * 把扁平訊息流整理成清單要畫的列：**同一串收合成一列，外層不重複**。
 *
 * 在此之前外層把整串的每一則各印一次，點開之後又把整串再印一次——同樣的訊息
 * 在畫面上出現兩遍。使用者要的是「討論串點開看就好，外層不用重複」。
 *
 * 兩個決定：
 *   1. **只收合「視窗裡不只一則」的串。** 私訊幾乎每則訊息各自成一個 thread
 *      （Google Chat 的行為），全部都收合的話整個清單都是折疊列，等於沒有清單。
 *   2. **收合列擺在該串「最後一則」的位置**，不是第一則。這個面板叫「最近訊息」，
 *      一串剛剛有人回過，就該讀起來是新的；擺在第一則會讓它沉到很上面。
 */
export function buildPreviewItems(messages: ChatMessage[]): PreviewItem[] {
  const counts = new Map<string, number>()
  const lastIndex = new Map<string, number>()
  messages.forEach((m, i) => {
    if (!m.thread_name) return
    counts.set(m.thread_name, (counts.get(m.thread_name) ?? 0) + 1)
    lastIndex.set(m.thread_name, i)
  })

  const isFolded = (m: ChatMessage) =>
    Boolean(m.thread_name) && (counts.get(m.thread_name!) ?? 0) >= 2

  // 編號依「收合列出現的先後」給，讀起來才跟畫面由上而下一致
  const order: string[] = []
  messages.forEach((m, i) => {
    if (isFolded(m) && lastIndex.get(m.thread_name!) === i) order.push(m.thread_name!)
  })

  const out: PreviewItem[] = []
  messages.forEach((m, i) => {
    if (!isFolded(m)) {
      out.push({ kind: 'message', key: m.name, message: m })
      return
    }
    const name = m.thread_name!
    // 只在該串的最後一則那個位置放一列，其餘位置略過（這就是「外層不重複」）
    if (lastIndex.get(name) !== i) return
    out.push({
      kind: 'thread',
      key: `thread:${name}`,
      threadName: name,
      index: order.indexOf(name) + 1,
      messages: messages.filter((x) => x.thread_name === name),
    })
  })
  return out
}
