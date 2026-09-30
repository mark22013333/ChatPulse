import { create } from 'zustand'
import { useAuthStore } from '@/store/auth'
import {
  DEFAULT_LIST_SORT,
  parseListSort,
  type ListSortPrefs,
  type MentionSortKey,
  type SpaceSortOptions,
} from '@/lib/listSort'

/**
 * 清單排序偏好（Space 清單與 Mention 收件匣），**每位 Viewer 各記一份**。
 *
 * 為什麼 key 要帶 Viewer id：同一台電腦、同一個瀏覽器可能先後登入不同的
 * Viewer（ADR-0001 每人用自己的 OAuth），共用一份 key 的話，後登入的人會
 * 繼承前一個人選的排序，而且看不出是從哪來的。
 *
 * 為什麼是 store 而不是元件 state：收件匣的顯示順序同時被 `[`／`]` 快捷鍵
 * （useGlobalHotkeys）用來決定「下一則」。兩處各自讀的話，畫面上排的和
 * 按 `]` 跳到的會是不同的下一則。
 */

const KEY_PREFIX = 'chatpulse.listSort.'

function storageKey(viewerId: number): string {
  return `${KEY_PREFIX}${viewerId}`
}

function read(viewerId: number): ListSortPrefs {
  try {
    return parseListSort(localStorage.getItem(storageKey(viewerId)))
  } catch {
    // 隱私模式、清過站台資料、或瀏覽器擋 storage（node 環境也走這裡）：當作沒設定過
    return DEFAULT_LIST_SORT
  }
}

function write(viewerId: number, prefs: ListSortPrefs) {
  try {
    localStorage.setItem(storageKey(viewerId), JSON.stringify(prefs))
  } catch {
    // 存不進去頂多下次要再選一次，不值得打斷 Viewer
  }
}

interface ListSortState {
  /**
   * 已讀進記憶體的偏好，以 Viewer id 為鍵。
   *
   * 沒讀過的 Viewer 不在這裡——由 `listSortOf` 第一次被問到時才讀 storage。
   * 這樣不必在登入流程裡找地方「初始化」，也不會有「還沒初始化就先 render
   * 了一次預設值、下一格才跳成 Viewer 的選擇」的閃動。
   */
  byViewer: Record<number, ListSortPrefs>
  setSpaceSort: (viewerId: number | null, patch: Partial<SpaceSortOptions>) => void
  setMentionSort: (viewerId: number | null, sort: MentionSortKey) => void
}

/**
 * 從 storage 讀出來後的快取。放在 store 外是因為 `listSortOf` 會在 selector
 * 裡被呼叫，而 selector 必須回傳**同一個參考**，否則 zustand 每次 render 都
 * 以為值變了、無限重繪。
 */
const loaded = new Map<number, ListSortPrefs>()

function current(state: ListSortState, viewerId: number | null): ListSortPrefs {
  if (viewerId === null) return DEFAULT_LIST_SORT
  const inStore = state.byViewer[viewerId]
  if (inStore) return inStore
  let prefs = loaded.get(viewerId)
  if (!prefs) {
    prefs = read(viewerId)
    loaded.set(viewerId, prefs)
  }
  return prefs
}

export const useListSortStore = create<ListSortState>((set, get) => ({
  byViewer: {},

  // viewerId=null（還沒登入）時什麼都不做：兩份清單只在登入後才畫得出來，
  // 而沒有 Viewer 就沒有「記在誰身上」可言
  setSpaceSort: (viewerId, patch) => {
    const prev = current(get(), viewerId)
    const next = { ...prev, space: { ...prev.space, ...patch } }
    if (viewerId === null) return
    write(viewerId, next)
    set((state) => ({ byViewer: { ...state.byViewer, [viewerId]: next } }))
  },

  setMentionSort: (viewerId, sort) => {
    const prev = current(get(), viewerId)
    const next = { ...prev, mention: sort }
    if (viewerId === null) return
    write(viewerId, next)
    set((state) => ({ byViewer: { ...state.byViewer, [viewerId]: next } }))
  },
}))

/** 這位 Viewer 目前的排序偏好。讀不到（沒登入、storage 被擋、內容壞掉）就是預設值。 */
export function listSortOf(state: ListSortState, viewerId: number | null): ListSortPrefs {
  return current(state, viewerId)
}

function viewerIdOf(state: ReturnType<typeof useAuthStore.getState>): number | null {
  return state.me?.viewer?.id ?? null
}

/**
 * 元件用：目前登入的 Viewer 的排序偏好，以及寫回它的兩個動作。
 *
 * 回傳的 `prefs` 在偏好沒變時是同一個參考，可以直接放進 useMemo 的相依。
 */
export function useViewerListSort() {
  const viewerId = useAuthStore(viewerIdOf)
  const prefs = useListSortStore((state) => current(state, viewerId))
  const setSpaceSort = useListSortStore((state) => state.setSpaceSort)
  const setMentionSort = useListSortStore((state) => state.setMentionSort)
  return {
    prefs,
    setSpaceSort: (patch: Partial<SpaceSortOptions>) => setSpaceSort(viewerId, patch),
    setMentionSort: (sort: MentionSortKey) => setMentionSort(viewerId, sort),
  }
}

/** 非元件用（快捷鍵的事件處理器）：當下這一刻的偏好。 */
export function viewerListSortNow(): ListSortPrefs {
  return current(useListSortStore.getState(), viewerIdOf(useAuthStore.getState()))
}

/** 測試用：清掉記憶體快取，模擬重新整理頁面後重新讀 storage。 */
export function resetListSortForTest() {
  loaded.clear()
  useListSortStore.setState({ byViewer: {} })
}
