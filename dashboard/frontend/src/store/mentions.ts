import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import type {
  CollectorRunStats,
  Mention,
  MentionCounts,
  MentionState,
  MentionStateValue,
} from '@/lib/types'

interface MentionsState {
  items: Mention[]
  counts: MentionCounts
  tab: MentionState
  loading: boolean
  /** 「立即檢查」進行中 */
  checking: boolean
  lastCheckStats: CollectorRunStats | null
  error: string | null
  selectedId: number | null

  /**
   * 勾起來要「一起回成一則」的 Mention id。
   *
   * 空陣列＝沒有在合併，草稿只回 `selectedId` 那一則。長度 ≥ 1 時，
   * 第一個是主要那則（回話會送到它的討論串），其餘一起被標成已處理。
   */
  mergeIds: number[]

  setTab: (tab: MentionState) => void
  select: (id: number | null) => void
  toggleMerge: (id: number) => void
  clearMerge: () => void
  /** 送出成功後把整組一起就地更新，不必重新拉整份清單 */
  applyResolvedMany: (mentions: Mention[]) => void
  /** 用 /api/v1/me 的 mention_counts 先把未處理數量填上，讓頂列 badge 不必等收件匣開啟 */
  seedCounts: (counts: MentionCounts) => void
  load: (options?: { silent?: boolean }) => Promise<void>
  checkNow: () => Promise<CollectorRunStats | null>
  setMentionState: (id: number, state: MentionState) => Promise<void>
  /** 送出回話成功後就地更新該則狀態 */
  applyResolved: (mention: Mention) => void
  /**
   * 清單以外的當前 mention（從摘要工作台按「產生回覆草稿」建立的）。
   * 那些是 state='manual'，收件匣刻意不列出來，但草稿工作區要畫得出來。
   */
  external: Mention | null
  selectExternal: (mention: Mention) => void

  /**
   * 開始自動重新拉取（規格 6.3 的輪詢節奏）。重複呼叫是安全的。
   *
   * 在此之前這個計時器住在 `MentionInbox` 的 useEffect 裡。輪詢屬於這份資料、
   * 不屬於那個畫面——住在元件裡的話，人在摘要工作台時頂列的未處理數字就不會
   * 動，而且元件一卸載重掛就重新開始計時。
   */
  startPolling: () => void
  stopPolling: () => void
}

/** 自動重新拉取間隔。 */
export const AUTO_RELOAD_MS = 45_000

// 模組層只存 handle，不在這裡碰 window——vitest 跑在 node 環境，
// module top-level 取用瀏覽器 API 會讓整個檔案 import 失敗。
let pollTimer: ReturnType<typeof setInterval> | null = null

export const useMentionsStore = create<MentionsState>((set, get) => ({
  items: [],
  counts: { pending: 0, resolved: 0 },
  tab: 'pending',
  loading: false,
  checking: false,
  lastCheckStats: null,
  error: null,
  selectedId: null,
  mergeIds: [],

  external: null,

  // 切換頁籤時清掉合併勾選：已處理那一頁勾起來要合併是沒有意義的，
  // 而留著看不見的勾選會讓下一次產草稿悄悄多回幾則
  setTab: (tab) => set({ tab, mergeIds: [] }),
  // 在收件匣點了別則，就不再是「清單外的那則」了，把 external 清掉
  select: (id) => set({ selectedId: id, external: null }),
  selectExternal: (mention) => set({ external: mention, selectedId: mention.id, mergeIds: [] }),

  startPolling: () => {
    if (pollTimer !== null) return
    void get().load()
    pollTimer = setInterval(() => void get().load({ silent: true }), AUTO_RELOAD_MS)
  },

  stopPolling: () => {
    if (pollTimer === null) return
    clearInterval(pollTimer)
    pollTimer = null
  },

  toggleMerge: (id) =>
    set((current) => {
      const next = current.mergeIds.includes(id)
        ? current.mergeIds.filter((x) => x !== id)
        : [...current.mergeIds, id]
      // 勾第一則時順便把它設成當前選取，讓右邊的工作區跟著顯示同一個對話
      return next.length === 1 ? { mergeIds: next, selectedId: next[0], external: null } : { mergeIds: next }
    }),

  clearMerge: () => set({ mergeIds: [] }),

  seedCounts: (counts) => {
    // 清單已經載入過就以清單為準，不要被 /me 的快照蓋回去
    if (get().items.length > 0) return
    set({ counts })
  },

  load: async (options = {}) => {
    if (!options.silent) set({ loading: true })
    set({ error: null })
    try {
      const data = await api.mentions({ with_content: true })
      set({
        items: data.mentions ?? [],
        counts: data.counts ?? { pending: 0, resolved: 0 },
      })
    } catch (err) {
      set({ error: errorMessage(err) })
    } finally {
      set({ loading: false })
    }
  },

  checkNow: async () => {
    set({ checking: true, error: null })
    try {
      const data = await api.refreshMentions()
      set({ lastCheckStats: data.stats ?? null })
      await get().load({ silent: true })
      return data.stats ?? null
    } catch (err) {
      set({ error: errorMessage(err) })
      return null
    } finally {
      set({ checking: false })
    }
  },

  setMentionState: async (id, state) => {
    try {
      const updated = await api.updateMention(id, state)
      set((current) => {
        const prev = current.items.find((item) => item.id === id)
        return {
          items: current.items.map((item) =>
            item.id === id
              ? { ...item, state: updated.state ?? state, resolved_at: updated.resolved_at ?? null }
              : item,
          ),
          counts: recount(current.counts, prev?.state ?? 'manual', state),
        }
      })
    } catch (err) {
      set({ error: errorMessage(err) })
      throw err
    }
  },

  applyResolvedMany: (mentions) => {
    // 逐則套用既有邏輯，計數才會一則一則正確地扣。整批直接覆寫的話，
    // 混著 manual 與 pending 時 counts 會失真而沒有任何跡象。
    for (const m of mentions) get().applyResolved(m)
    set({ mergeIds: [] })
  },

  applyResolved: (mention) => {
    set((current) => {
      const prev = current.items.find((item) => item.id === mention.id)
      // 不在清單裡代表它原本是 manual（從摘要工作台挑的草稿目標），
      // 收件匣刻意沒列出它。現在回話送出、狀態變成已處理了，就該補進清單——
      // 否則使用者回覆完之後在「已處理」找不到自己剛做的事。
      const from: MentionStateValue = prev?.state ?? 'manual'
      const merged = { ...(prev ?? mention), ...mention }
      return {
        items: prev
          ? current.items.map((item) => (item.id === mention.id ? merged : item))
          : [merged, ...current.items],
        counts: recount(current.counts, from, mention.state),
      }
    })
  },
}))

/**
 * 這則還沒處理完嗎。
 *
 * `manual`（從摘要工作台按「產生回覆草稿」挑的）算待處理——使用者按下那個
 * 按鈕的意思就是「我要回這則」，與被 @ 一樣是一件待辦。後端的 list_mentions
 * 與 count_mentions 都是這樣算的，前端三處判準必須跟它一致。
 *
 * 這個 export 存在的理由就是「不要有第二份定義」：計數（bucket）、分頁歸類
 * （selectMentionsByState）、收件匣的按鈕文案三處曾經各寫各的，其中按鈕那處
 * 漏了 manual，於是自選對話在待處理分頁裡顯示成「退回待處理」。
 */
export function isOutstanding(state: MentionStateValue): boolean {
  return state !== 'resolved'
}

/**
 * 依「從哪個狀態變到哪個狀態」重算計數。
 *
 * 一定要知道 `from`：只看新狀態的話，任何東西變成 resolved 都會把 pending
 * 減一，包括本來就不在 pending 的項目——數字會慢慢失真而沒人發現。
 */
function bucket(state: MentionStateValue): keyof MentionCounts {
  return isOutstanding(state) ? 'pending' : 'resolved'
}

function recount(
  counts: MentionCounts,
  from: MentionStateValue,
  to: MentionStateValue,
): MentionCounts {
  const next = { ...counts }
  const a = bucket(from)
  const b = bucket(to)
  if (a === b) return next // 同一格內移動（例如 pending -> manual），數字不變
  next[a] = Math.max(0, next[a] - 1)
  next[b] += 1
  return next
}

export function selectMentionsByState(items: Mention[], state: MentionState): Mention[] {
  return items
    .filter((item) =>
      // manual（自己從摘要工作台挑的草稿目標）歸在待處理，
      // 與後端的 list_mentions 保持一致——兩邊分歧會讓計數對不上清單
      state === 'pending' ? isOutstanding(item.state) : item.state === state,
    )
    .sort((a, b) => new Date(b.create_time).getTime() - new Date(a.create_time).getTime())
}
