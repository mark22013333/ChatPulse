import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import type { CollectorRunStats, Mention, MentionCounts, MentionState } from '@/lib/types'

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
      set((current) => ({
        items: current.items.map((item) =>
          item.id === id
            ? { ...item, state: updated.state ?? state, resolved_at: updated.resolved_at ?? null }
            : item,
        ),
        counts: recount(current.counts, state),
      }))
    } catch (err) {
      set({ error: errorMessage(err) })
      throw err
    }
  },

  applyResolved: (mention) => {
    set((current) => ({
      items: current.items.map((item) =>
        item.id === mention.id
          ? { ...item, state: mention.state, resolved_at: mention.resolved_at }
          : item,
      ),
      counts: recount(current.counts, mention.state),
    }))
  },
}))

function recount(counts: MentionCounts, moveTo: MentionState): MentionCounts {
  if (moveTo === 'resolved') {
    return { pending: Math.max(0, counts.pending - 1), resolved: counts.resolved + 1 }
  }
  return { pending: counts.pending + 1, resolved: Math.max(0, counts.resolved - 1) }
}

export function selectMentionsByState(items: Mention[], state: MentionState): Mention[] {
  return items
    .filter((item) => item.state === state)
    .sort((a, b) => new Date(b.create_time).getTime() - new Date(a.create_time).getTime())
}
