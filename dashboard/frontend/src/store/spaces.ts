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
