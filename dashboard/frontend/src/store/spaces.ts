import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import type { Space } from '@/lib/types'

interface SpacesState {
  items: Space[]
  total: number
  cached: boolean
  cachedAt: string | null
  loading: boolean
  /** 手動強制刷新（?refresh=true）進行中 */
  refreshing: boolean
  error: string | null
  /** 名稱模糊搜尋（在前端過濾，436 筆一次抓回來後不再往返後端） */
  search: string
  selectedId: string | null

  /** 釘選寫入進行中 */
  pinning: boolean

  load: (options?: { refresh?: boolean }) => Promise<void>
  setSearch: (value: string) => void
  select: (id: string | null) => void
  /** 給空間取別名。傳空字串＝清除，回到自動辨識的名字。 */
  rename: (spaceId: string, alias: string) => Promise<void>
  /**
   * 切換釘選。
   *
   * 後端從一開始就支援（`preferences.pinned_space_ids` 有 DB 欄位、
   * `GET /spaces` 每一筆都帶 `pinned`），只是前端一直沒有介面。
   */
  togglePin: (space: Space) => Promise<void>
}

export const useSpacesStore = create<SpacesState>((set, get) => ({
  items: [],
  total: 0,
  cached: false,
  cachedAt: null,
  loading: false,
  refreshing: false,
  error: null,
  search: '',
  selectedId: null,
  pinning: false,

  load: async (options = {}) => {
    const refresh = options.refresh === true
    set(refresh ? { refreshing: true, error: null } : { loading: true, error: null })
    try {
      const data = await api.spaces(refresh ? { refresh: true } : {})
      set({
        items: data.spaces ?? [],
        total: data.total ?? data.count ?? (data.spaces?.length ?? 0),
        cached: data.cached ?? false,
        cachedAt: data.cached_at ?? null,
      })
    } catch (err) {
      set({ error: errorMessage(err) })
    } finally {
      set({ loading: false, refreshing: false })
    }
  },

  setSearch: (value) => set({ search: value }),
  select: (id) => set({ selectedId: id }),

  rename: async (spaceId, alias) => {
    const trimmed = alias.trim()
    try {
      await api.setSpaceAlias({ space_id: spaceId, alias: trimmed })
    } catch (err) {
      set({ error: errorMessage(err) })
      return
    }
    // 就地更新，不重抓整份清單——後端已經清掉快取，但重抓 436 筆只為了
    // 改一個名字太浪費，而且會讓捲動位置跳掉。
    set((state) => ({
      items: state.items.map((s) =>
        s.id === spaceId
          ? {
              ...s,
              // 清除別名時先顯示佔位字串；下次載入清單才會拿回自動辨識的結果
              displayName: trimmed || (s.type === 'DIRECT_MESSAGE' ? '（私訊）' : '（未命名空間）'),
              nameSource: trimmed ? 'dm_manual' : null,
            }
          : s,
      ),
    }))
  },

  togglePin: async (space) => {
    const next = !space.pinned
    const ids = get()
      .items.filter((s) => (s.id === space.id ? next : s.pinned))
      .map((s) => s.id)

    set({ pinning: true, error: null })
    try {
      // **一定要送陣列，不能送 null。** `pinned_space_ids` 的 null 語意是
      // 「不改」（與四個回覆設定欄位相反，見 api-contract 的對照表），
      // 所以取消最後一個釘選要送 `[]`，送 null 會靜默地什麼都沒發生。
      await api.updatePreferences({ pinned_space_ids: ids })
      set((state) => ({
        items: state.items.map((s) => (s.id === space.id ? { ...s, pinned: next } : s)),
      }))
    } catch (err) {
      set({ error: errorMessage(err) })
    } finally {
      set({ pinning: false })
    }
  },
}))

/** 依搜尋字串過濾（不分大小寫）。排序見 `sortByPinned`。 */
export function filterSpaces(items: Space[], search: string): Space[] {
  const keyword = search.trim().toLowerCase()
  const filtered = keyword
    ? items.filter((space) => (space.displayName ?? '').toLowerCase().includes(keyword))
    : items
  return filtered
}

/**
 * 把釘選的排前面，其餘維持原順序（後端已依最後活動時間排好）。
 *
 * 刻意**不**塞進 `filterSpaces`：那支有 14 項既有測試打在上面，而排序與過濾
 * 是兩件事。呼叫端自己組合 `sortByPinned(filterSpaces(...))`。
 */
export function sortByPinned(items: Space[]): Space[] {
  if (!items.some((space) => space.pinned)) return items
  const pinned: Space[] = []
  const rest: Space[] = []
  for (const space of items) (space.pinned ? pinned : rest).push(space)
  return [...pinned, ...rest]
}

export function findSpace(items: Space[], id: string | null): Space | null {
  if (!id) return null
  return items.find((space) => space.id === id) ?? null
}
