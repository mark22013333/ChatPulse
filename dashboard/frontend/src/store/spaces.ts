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

  load: (options?: { refresh?: boolean }) => Promise<void>
  setSearch: (value: string) => void
  select: (id: string | null) => void
  /** 給空間取別名。傳空字串＝清除，回到自動辨識的名字。 */
  rename: (spaceId: string, alias: string) => Promise<void>
}

export const useSpacesStore = create<SpacesState>((set) => ({
  items: [],
  total: 0,
  cached: false,
  cachedAt: null,
  loading: false,
  refreshing: false,
  error: null,
  search: '',
  selectedId: null,

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
}))

/** 依搜尋字串過濾（不分大小寫），並把已釘選的排前面。 */
export function filterSpaces(items: Space[], search: string): Space[] {
  const keyword = search.trim().toLowerCase()
  const filtered = keyword
    ? items.filter((space) => (space.displayName ?? '').toLowerCase().includes(keyword))
    : items
  return filtered
}

export function findSpace(items: Space[], id: string | null): Space | null {
  if (!id) return null
  return items.find((space) => space.id === id) ?? null
}
