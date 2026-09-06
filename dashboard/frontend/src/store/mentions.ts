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

  setTab: (tab: MentionState) => void
  select: (id: number | null) => void
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
}

export const useMentionsStore = create<MentionsState>((set, get) => ({
  items: [],
  counts: { pending: 0, resolved: 0 },
  tab: 'pending',
  loading: false,
  checking: false,
  lastCheckStats: null,
  error: null,
  selectedId: null,

  external: null,

  setTab: (tab) => set({ tab }),
  // 在收件匣點了別則，就不再是「清單外的那則」了，把 external 清掉
  select: (id) => set({ selectedId: id, external: null }),
  selectExternal: (mention) => set({ external: mention, selectedId: mention.id }),

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
 * 依「從哪個狀態變到哪個狀態」重算計數。
 *
 * 一定要知道 `from`：以前這裡只看新狀態，變成 resolved 就把 pending 減一。
 * 但從摘要工作台挑的草稿目標（state='manual'）本來就不算在待處理裡，
 * 送出回話後被減這一下，「待處理」的數字會平白少一個。
 * manual 不加也不減——它不屬於收件匣的任何分頁。
 */
function recount(
  counts: MentionCounts,
  from: MentionStateValue,
  to: MentionStateValue,
): MentionCounts {
  const next = { ...counts }
  if (from === 'pending') next.pending = Math.max(0, next.pending - 1)
  if (from === 'resolved') next.resolved = Math.max(0, next.resolved - 1)
  if (to === 'pending') next.pending += 1
  if (to === 'resolved') next.resolved += 1
  return next
}

export function selectMentionsByState(items: Mention[], state: MentionState): Mention[] {
  return items
    .filter((item) => item.state === state)
    .sort((a, b) => new Date(b.create_time).getTime() - new Date(a.create_time).getTime())
}
