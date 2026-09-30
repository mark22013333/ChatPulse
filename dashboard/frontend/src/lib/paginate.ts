/**
 * 表格的「篩選 → 排序 → 切頁」（純函式，不碰 store、不碰 storage、不碰 DOM）。
 *
 * 🔴 **順序不能換**：一定是先對全部資料篩選、再對全部資料排序、最後才切出
 * 當頁。反過來「先切 25 筆再排」的話，畫面上看起來是全部 Space 依最後活動
 * 排好了，實際只是第一頁那 25 筆彼此之間排好——第二頁上真正最新的那一個
 * 永遠不會出現在第一列，而且沒有任何錯誤訊息。這正是 CLAUDE.md 硬規則的
 * 「靜默少給資料」。`paginate.test.ts` 用「只出現在第二頁的資料」當正對照守著。
 *
 * 為什麼抽成純函式、元件只負責呈現：S3（摘要工作台）與 S4（工時草稿）都要
 * 用同一套流程，放在元件裡就得各測一次；而且 jsdom 裡要造出「第二頁」得畫
 * 26 列以上，純函式測試只要一個陣列。
 */

/** 分頁器可選的每頁筆數（設計規格 §4.2）。 */
export const PAGE_SIZES = [10, 25, 50] as const
export type PageSize = (typeof PAGE_SIZES)[number]
export const DEFAULT_PAGE_SIZE: PageSize = 25

/**
 * 把外來的值（storage 讀出來的、Select 給的字串）還原成合法的每頁筆數；
 * 認不得回 `null`，由呼叫端決定要退回預設還是忽略這次變更。
 *
 * ⚠️ 不能 `Number(x)` 了事：Base UI 的 Select 清除選擇時給 `null`，
 * `Number(null)` 是 0，切頁時 `slice(0, 0)` 就是一頁空白、總頁數算成 Infinity。
 * 所以只接受清單裡那三個值。
 */
export function parsePageSize(raw: unknown): PageSize | null {
  const n = typeof raw === 'string' && raw.trim() !== '' ? Number(raw) : raw
  return PAGE_SIZES.includes(n as PageSize) ? (n as PageSize) : null
}

/**
 * 總頁數。**0 筆也算 1 頁**：分頁器要顯示「第 1 頁」而不是「第 1／0 頁」，
 * 頁碼夾回時也才有一個合法的落點。
 */
export function pageCountOf(total: number, pageSize: number): number {
  if (!(pageSize > 0)) return 1
  return Math.max(1, Math.ceil(Math.max(total, 0) / pageSize))
}

/**
 * 把頁碼（從 1 起算）夾進 `1..pageCount`。
 *
 * 篩選條件一收窄，總頁數可能從 5 掉到 2，而 Viewer 還停在第 4 頁——
 * 不夾的話 `slice` 會回空陣列，畫面顯示「共 30 筆」卻一列都沒有，
 * 看起來像篩選壞了。夾到最後一頁，至少看得到資料。NaN／小數也在這裡收掉。
 */
export function clampPage(page: number, pageCount: number): number {
  if (!Number.isFinite(page)) return 1
  return Math.min(Math.max(Math.trunc(page), 1), Math.max(pageCount, 1))
}

export interface PaginateOptions<T> {
  /** 篩選條件；省略＝全部保留 */
  filter?: (item: T) => boolean
  /**
   * 排序整份（已篩選的）清單，回傳新陣列。用「整份清單進、整份清單出」而不是
   * comparator，是為了直接接 `lib/listSort.ts` 的 `sortSpaces(items, options)`——
   * 它的「釘選置頂」是分組後各自排序，不是單一 comparator 表達得了的。
   * 省略＝維持傳入順序。
   */
  sort?: (items: readonly T[]) => readonly T[]
  /** 目前頁碼，從 1 起算；超出範圍會被夾回（見 clampPage） */
  page: number
  pageSize: number
}

export interface PageResult<T> {
  /** 當頁要畫的列 */
  items: T[]
  /** 夾回之後實際的頁碼。呼叫端的 state 若與它不同，應該以它為準 */
  page: number
  pageCount: number
  /** 🔴 篩選後的**總數**，給「共 N 筆」用——不是當頁筆數 */
  total: number
  /** 當頁第一列在整份（篩選、排序後）清單裡的 index，從 0 起算 */
  offset: number
}

/** 篩選 → 排序 → 切頁。回傳新陣列，不改動傳入的陣列（它通常是 store 的 state）。 */
export function paginate<T>(items: readonly T[], options: PaginateOptions<T>): PageResult<T> {
  const filtered = options.filter ? items.filter(options.filter) : [...items]
  const sorted = options.sort ? options.sort(filtered) : filtered
  const pageSize = options.pageSize > 0 ? options.pageSize : DEFAULT_PAGE_SIZE
  const total = sorted.length
  const pageCount = pageCountOf(total, pageSize)
  const page = clampPage(options.page, pageCount)
  const offset = (page - 1) * pageSize
  return { items: sorted.slice(offset, offset + pageSize), page, pageCount, total, offset }
}

/** 頁碼列的一格：數字是頁碼，`'gap'` 是省略號。 */
export type PageToken = number | 'gap'

/**
 * 分頁器要列出哪些頁碼：永遠有第一頁、最後一頁、目前頁與前後各 `siblings` 頁，
 * 其餘折成省略號。
 *
 * 為什麼不全列：摘要工作台有 436 個 Space，每頁 10 筆就是 44 個按鈕，
 * 分頁器會換行成兩三排。只差一頁的缺口直接補上那一頁——
 * 「1 … 3」裡的省略號只代表一頁，比直接寫 2 還難懂。
 */
export function pageTokens(page: number, pageCount: number, siblings = 1): PageToken[] {
  const count = Math.max(pageCount, 1)
  const current = clampPage(page, count)
  const wanted = new Set<number>([1, count])
  for (let p = current - siblings; p <= current + siblings; p++) {
    if (p >= 1 && p <= count) wanted.add(p)
  }
  const pages = [...wanted].sort((a, b) => a - b)
  const out: PageToken[] = []
  pages.forEach((p, i) => {
    const prev = pages[i - 1]
    if (prev !== undefined) {
      if (p - prev === 2) out.push(prev + 1)
      else if (p - prev > 2) out.push('gap')
    }
    out.push(p)
  })
  return out
}

// ── 欄位排序狀態（表頭按鈕與 aria-sort） ──────────────────────────────

export type SortDirection = 'asc' | 'desc'

export interface SortState<K extends string = string> {
  key: K
  direction: SortDirection
}

/**
 * 點一下表頭之後的排序狀態：點別的欄 → 換成那一欄、用它的起始方向；
 * 點同一欄 → 反轉方向。
 *
 * 為什麼不做第三態「取消排序」：取消之後的順序是「後端恰好回的順序」，
 * 而 lib/listSort.ts 開頭記過，那正是 2026-09-30 Viewer 回報「看起來不像
 * 依時間」的來源。表格永遠處在某一種明確的排序。
 * `initial` 讓時間、數字欄第一次點就是新→舊／大→小，那才是會被點的原因。
 */
export function toggleSort<K extends string>(
  current: SortState<K> | null,
  key: K,
  initial: SortDirection = 'asc',
): SortState<K> {
  if (current && current.key === key) {
    return { key, direction: current.direction === 'asc' ? 'desc' : 'asc' }
  }
  return { key, direction: initial }
}

/**
 * 某一欄的 `aria-sort` 值。**沒在排的欄回 `undefined`（不輸出屬性），不回 `'none'`**：
 * WAI-ARIA 建議同一時間只有一欄帶 aria-sort，每一欄都掛 `none` 會讓讀屏
 * 逐欄唸「未排序」。
 */
export function ariaSortOf<K extends string>(
  current: SortState<K> | null,
  key: K,
): 'ascending' | 'descending' | undefined {
  if (!current || current.key !== key) return undefined
  return current.direction === 'asc' ? 'ascending' : 'descending'
}
