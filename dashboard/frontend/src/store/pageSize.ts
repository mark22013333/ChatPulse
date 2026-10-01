import { create } from 'zustand'
import { useAuthStore } from '@/store/auth'
import { DEFAULT_PAGE_SIZE, parsePageSize, type PageSize } from '@/lib/paginate'

/**
 * 表格的每頁筆數偏好，**每位 Viewer、每張表格各記一份**（設計規格 §4.2）。
 * 寫法照 `store/listSort.ts`：key 帶 Viewer id、讀取延後到第一次被問、
 * selector 回同一個參考。
 *
 * 為什麼 key 要帶 Viewer id：同一個瀏覽器可能先後登入不同的 Viewer
 * （ADR-0001 每人用自己的 OAuth），共用一份的話後登入的人會繼承前一個人
 * 的設定，而且看不出是從哪來的。
 *
 * 為什麼每張表格各一份、不共用：摘要工作台有幾百個 Space，工時草稿一個月
 * 才二十幾天——在一張表選了 50，不代表另一張也想要 50。
 */

/** 會用到分頁器的表格。新增表格時加在這裡，storage 裡認不得的 id 會被丟掉。 */
export const TABLE_IDS = ['summary', 'worklog'] as const
export type TableId = (typeof TABLE_IDS)[number]

export type PageSizePrefs = Partial<Record<TableId, PageSize>>

const KEY_PREFIX = 'chatpulse.pageSize.'
const EMPTY: PageSizePrefs = {}

function storageKey(viewerId: number): string {
  return `${KEY_PREFIX}${viewerId}`
}

/**
 * 把 storage 讀出來的字串還原成偏好。**逐欄驗證、認不得的欄位丟掉**
 * （丟掉＝那張表用預設 25）。
 *
 * ⚠️ 不能 `JSON.parse(raw) as PageSizePrefs`：手改過的、舊版本存的 `0` 或
 * `"abc"` 會原樣流進 `slice`，一頁空白而且不報錯。一欄壞掉只退那一欄。
 */
export function parsePageSizePrefs(raw: string | null): PageSizePrefs {
  if (!raw) return EMPTY
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    return EMPTY
  }
  if (typeof data !== 'object' || data === null || Array.isArray(data)) return EMPTY
  const out: PageSizePrefs = {}
  for (const id of TABLE_IDS) {
    const size = parsePageSize((data as Record<string, unknown>)[id])
    if (size !== null) out[id] = size
  }
  return out
}

function read(viewerId: number): PageSizePrefs {
  try {
    return parsePageSizePrefs(localStorage.getItem(storageKey(viewerId)))
  } catch {
    // 隱私模式、清過站台資料、或瀏覽器擋 storage（node 環境也走這裡）：當作沒設定過
    return EMPTY
  }
}

function write(viewerId: number, prefs: PageSizePrefs) {
  try {
    localStorage.setItem(storageKey(viewerId), JSON.stringify(prefs))
  } catch {
    // 存不進去頂多下次要再選一次，不值得打斷 Viewer
  }
}

interface PageSizeState {
  /** 已讀進記憶體的偏好，以 Viewer id 為鍵；沒讀過的由 `current` 第一次被問時才讀 */
  byViewer: Record<number, PageSizePrefs>
  setPageSize: (viewerId: number | null, table: TableId, size: PageSize) => void
}

/**
 * storage 讀出來後的快取。放在 store 外的理由同 store/listSort.ts：selector
 * 必須回傳同一個參考，否則 zustand 每次 render 都以為值變了、無限重繪。
 */
const loaded = new Map<number, PageSizePrefs>()

function current(state: PageSizeState, viewerId: number | null): PageSizePrefs {
  if (viewerId === null) return EMPTY
  const inStore = state.byViewer[viewerId]
  if (inStore) return inStore
  let prefs = loaded.get(viewerId)
  if (!prefs) {
    prefs = read(viewerId)
    loaded.set(viewerId, prefs)
  }
  return prefs
}

export const usePageSizeStore = create<PageSizeState>((set, get) => ({
  byViewer: {},

  // viewerId=null（還沒登入）時什麼都不做：沒有 Viewer 就沒有「記在誰身上」可言
  setPageSize: (viewerId, table, size) => {
    if (viewerId === null) return
    const next = { ...current(get(), viewerId), [table]: size }
    write(viewerId, next)
    set((state) => ({ byViewer: { ...state.byViewer, [viewerId]: next } }))
  },
}))

/** 這位 Viewer 在這張表的每頁筆數。讀不到（沒登入、storage 被擋、內容壞掉）就是 25。 */
export function pageSizeOf(
  state: PageSizeState,
  viewerId: number | null,
  table: TableId,
): PageSize {
  return current(state, viewerId)[table] ?? DEFAULT_PAGE_SIZE
}

function viewerIdOf(state: ReturnType<typeof useAuthStore.getState>): number | null {
  return state.me?.viewer?.id ?? null
}

/** 元件用：目前登入的 Viewer 在這張表的每頁筆數，以及寫回它的動作。 */
export function useViewerPageSize(table: TableId) {
  const viewerId = useAuthStore(viewerIdOf)
  // 回傳的是數字（原始值），selector 不會有參考不穩的問題
  const pageSize = usePageSizeStore((state) => pageSizeOf(state, viewerId, table))
  const setPageSize = usePageSizeStore((state) => state.setPageSize)
  return {
    pageSize,
    setPageSize: (size: PageSize) => setPageSize(viewerId, table, size),
  }
}

/** 測試用：清掉記憶體快取，模擬重新整理頁面後重新讀 storage。 */
export function resetPageSizeForTest() {
  loaded.clear()
  usePageSizeStore.setState({ byViewer: {} })
}
