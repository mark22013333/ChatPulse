import { spaceTypeLabel } from '@/lib/format'
import type { SortState } from '@/lib/paginate'
import type { SpaceSortKey } from '@/lib/listSort'
import type { Mention, MentionStateValue, Space } from '@/lib/types'

/**
 * 摘要工作台的 Space 表格要用的純函式（設計規格 2026-10-01 §4.2、§4.3）。
 *
 * 不碰 store、不碰 DOM：篩選條件怎麼判、表頭排序怎麼對回 `lib/listSort.ts`
 * 的排序鍵、最後活動怎麼顯示，全在這裡測。元件只負責把它們接起來。
 *
 * ⚠️ 這支**不切頁**。切頁一律交給 `lib/paginate.ts`，這裡給的 filter 只是
 * 「一筆要不要留下」的判斷式——它會先對全部 Space 跑完，paginate 才切頁。
 */

// ── 模組內 tabs（子視圖，不是模組切換；規格 §1） ────────────────────────

export const SPACE_TABS = ['all', 'mentions', 'pinned'] as const
export type SpaceTab = (typeof SPACE_TABS)[number]

export const SPACE_TAB_LABELS: Record<SpaceTab, string> = {
  all: '全部 Space',
  mentions: '有新 Mention',
  pinned: '已釘選',
}

/** Base UI Tabs 給的值是 unknown；認不得就回 null，由呼叫端決定不動。 */
export function parseSpaceTab(raw: unknown): SpaceTab | null {
  return SPACE_TABS.includes(raw as SpaceTab) ? (raw as SpaceTab) : null
}

// ── 類型篩選 ──────────────────────────────────────────────────────────

/**
 * 類型篩選的選項。**值就是 `spaceTypeLabel` 的輸出**，不另訂一套代碼：
 * Google 對私訊有 `DIRECT_MESSAGE` 與 `DM` 兩種寫法，比對標籤才能讓兩者
 * 都落在「私訊」底下；另訂代碼就得在這裡再寫一次那張對照表，兩份會漂移。
 *
 * 認不得的類型（spaceTypeLabel 會原樣回傳）只在「全部類型」出現，
 * 不會被任何一個選項吃掉，也不會消失。
 */
export const SPACE_TYPE_FILTERS = ['all', '群組', '多人對話', '私訊'] as const
export type SpaceTypeFilter = (typeof SPACE_TYPE_FILTERS)[number]

export const SPACE_TYPE_FILTER_LABELS: Record<SpaceTypeFilter, string> = {
  all: '全部類型',
  群組: '群組',
  多人對話: '多人對話',
  私訊: '私訊',
}

export function parseSpaceTypeFilter(raw: unknown): SpaceTypeFilter | null {
  return SPACE_TYPE_FILTERS.includes(raw as SpaceTypeFilter) ? (raw as SpaceTypeFilter) : null
}

// ── 每個 Space 的待處理 Mention 數 ─────────────────────────────────────

/**
 * 每個 Space 有幾則**待處理**的 Mention。
 *
 * 判準由呼叫端傳入 `store/mentions.ts` 的 `isOutstanding`（pending ＋ manual），
 * 不在這裡另寫一份——那支 export 存在的理由就是「不要有第二份定義」。
 * 用參數傳而不是 import：`lib/` 底下的非測試檔一律不 import store，
 * 這支開了頭，純函式層就開始背 zustand 與 api 的相依。
 *
 * 🔴 這是「已載入的 Mention」裡的數，不是後端的全部：`GET /mentions` 預設只回
 * 最新 200 筆（見 `lib/listSort.ts` 的 `mentionTruncation`）。呼叫端要用
 * `mentionTruncation` 判斷有沒有被截斷，被截斷時**明示**，不能讓 0 看起來
 * 像「這個 Space 沒有 Mention」。
 */
export function outstandingBySpace(
  mentions: readonly Mention[],
  isOutstanding: (state: MentionStateValue) => boolean,
): Map<string, number> {
  const out = new Map<string, number>()
  for (const mention of mentions) {
    if (!isOutstanding(mention.state)) continue
    out.set(mention.space_id, (out.get(mention.space_id) ?? 0) + 1)
  }
  return out
}

// ── 篩選 ──────────────────────────────────────────────────────────────

export interface SpaceTableFilters {
  tab: SpaceTab
  search: string
  type: SpaceTypeFilter
}

export const DEFAULT_SPACE_TABLE_FILTERS: SpaceTableFilters = {
  tab: 'all',
  search: '',
  type: 'all',
}

/**
 * 產生「這一筆要不要留下」的判斷式，給 `paginate` 的 `filter` 用。
 *
 * 搜尋比的是 `displayName`，與舊的左欄（`store/spaces.ts` 的 `filterSpaces`）
 * 同一套規則：不分大小寫、前後空白不算。
 */
export function spaceTableFilter(
  filters: SpaceTableFilters,
  outstanding: ReadonlyMap<string, number>,
): (space: Space) => boolean {
  const keyword = filters.search.trim().toLowerCase()
  return (space) => {
    if (filters.tab === 'pinned' && !space.pinned) return false
    if (filters.tab === 'mentions' && !((outstanding.get(space.id) ?? 0) > 0)) return false
    if (filters.type !== 'all' && spaceTypeLabel(space.type) !== filters.type) return false
    if (keyword && !(space.displayName ?? '').toLowerCase().includes(keyword)) return false
    return true
  }
}

// ── 表頭排序 ↔ listSort 的排序鍵 ───────────────────────────────────────

/** 表格上可以點來排序的欄。 */
export type SpaceColumnKey = 'name' | 'type' | 'activity'

/**
 * 目前的排序鍵對應到哪一欄、哪個方向（給 DataTable 的 `aria-sort`）。
 *
 * 排序本身沿用 `lib/listSort.ts` 的 `sortSpaces`（規格 §4.2），偏好也沿用
 * 同一份（舊左欄選過的排序，換成表格之後仍然有效）。所以這裡只做「翻譯」，
 * 不另立一套排序狀態——兩份狀態就會有「表頭說依名稱、實際依時間」的時候。
 */
export function headerSortOf(sort: SpaceSortKey): SortState<SpaceColumnKey> {
  switch (sort) {
    case 'activity-desc':
      return { key: 'activity', direction: 'desc' }
    case 'activity-asc':
      return { key: 'activity', direction: 'asc' }
    case 'name':
      return { key: 'name', direction: 'asc' }
    case 'type':
      return { key: 'type', direction: 'asc' }
    default: {
      const exhaustive: never = sort
      void exhaustive
      return { key: 'activity', direction: 'desc' }
    }
  }
}

/**
 * 點了某一欄的表頭之後，新的排序鍵是什麼。
 *
 * - 最後活動：從別欄點過來＝新→舊（會點它就是想看最近在動的）；再點一次反轉。
 * - 名稱、類型：`listSort.ts` 只有一個方向，再點一次**維持不變**。
 *
 * ⚠️ 被否決的做法：名稱／類型用「把排好的結果整份反轉」做出降冪。
 * `sortSpaces` 的類型排序是「組內依最新活動」、最後活動是「沒有時間的永遠
 * 排最後」，整份反轉會把這兩條規則一起翻過來，得到一個誰都沒設計過的順序。
 * 要雙向就該在 listSort 補真的排序鍵，那屬於 listSort 的範圍，這次不動。
 */
export function nextSpaceSort(current: SpaceSortKey, clicked: SpaceColumnKey): SpaceSortKey {
  if (clicked === 'activity') {
    return current === 'activity-desc' ? 'activity-asc' : 'activity-desc'
  }
  return clicked
}

// ── 最後活動的顯示 ─────────────────────────────────────────────────────

/**
 * 最後活動：**今天顯示時間，更早顯示日期**（規格 §4.3）。跨年才帶年份——
 * 表格裡「9/28」比「2026/9/28」好掃，但去年的 9/28 不能跟今年的混在一起。
 *
 * 「今天」以瀏覽器本地時區判斷，與 `formatDateTime` 一致。解析不了一律 '—'，
 * 不猜。`now` 可注入，測試才不必跟著真實時鐘跑。
 */
export function lastActiveLabel(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  if (sameDay) return `${pad(date.getHours())}:${pad(date.getMinutes())}`
  const md = `${date.getMonth() + 1}/${date.getDate()}`
  return date.getFullYear() === now.getFullYear() ? md : `${date.getFullYear()}/${md}`
}
