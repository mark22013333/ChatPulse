import type { Mention, MentionCounts, Space } from '@/lib/types'

/**
 * Space 清單與 Mention 收件匣的排序（純函式，不碰 store、不碰 storage）。
 *
 * 為什麼另立一支、不塞回 `store/spaces.ts` 的 `sortByPinned`：那支的契約是
 * 「保留後端給的順序、只把釘選拉前面」，而 2026-09-30 Viewer 回報的問題正是
 * 這個契約本身——後端（Google 的 spaces.list）**從沒保證**依最後活動排序，
 * 於是畫面上「釘選 22 小時前」排在「未釘選 5 分鐘前」之前，看起來不像依時間。
 * 這裡改成前端自己依欄位排，順序不再取決於上游恰好回了什麼。
 * `sortByPinned` 仍留給設定頁（SpacePrefsPage）用，行為不變。
 */

/**
 * ⚠️ `name`／`type` 是升冪，降冪另立 `name-desc`／`type-desc`，**不把舊鍵改名成
 * `name-asc`**：這些值原樣存在 Viewer 的 localStorage 裡，改名會讓每個選過
 * 「名稱」的人在升級後被 `parseListSort` 默默退回預設排序。
 */
export type SpaceSortKey =
  | 'activity-desc'
  | 'activity-asc'
  | 'name'
  | 'name-desc'
  | 'type'
  | 'type-desc'
export type MentionSortKey = 'time-desc' | 'time-asc' | 'space' | 'sender'

export interface SpaceSortOptions {
  sort: SpaceSortKey
  /** 釘選置頂。預設關閉：Viewer 要的是「依最新時間」，釘選不該插隊 */
  pinnedFirst: boolean
}

export const SPACE_SORT_KEYS: readonly SpaceSortKey[] = [
  'activity-desc',
  'activity-asc',
  'name',
  'name-desc',
  'type',
  'type-desc',
]
export const MENTION_SORT_KEYS: readonly MentionSortKey[] = [
  'time-desc',
  'time-asc',
  'space',
  'sender',
]

export const DEFAULT_SPACE_SORT: SpaceSortOptions = { sort: 'activity-desc', pinnedFirst: false }
export const DEFAULT_MENTION_SORT: MentionSortKey = 'time-desc'

export const SPACE_SORT_LABELS: Record<SpaceSortKey, string> = {
  'activity-desc': '最後活動（新→舊）',
  'activity-asc': '最後活動（舊→新）',
  name: '名稱',
  'name-desc': '名稱（反序）',
  type: '類型',
  'type-desc': '類型（反序）',
}

export const MENTION_SORT_LABELS: Record<MentionSortKey, string> = {
  'time-desc': '時間（新→舊）',
  'time-asc': '時間（舊→新）',
  space: '依 Space',
  sender: '依發言者',
}

// ⚠️ 不能用 `a < b` 比中文名字：那是 UTF-16 code unit 順序，與任何人認得的
// 排法都對不上。numeric 讓「第 2 組」排在「第 10 組」前面。
const collator = new Intl.Collator('zh-Hant-TW', { numeric: true, sensitivity: 'base' })

/**
 * 解析 ISO 時間。解析不了一律當成「沒有時間」回 null。
 *
 * ⚠️ 不能讓 NaN 流進比較：`NaN - x` 是 NaN，Array.sort 拿到 NaN 的比較結果
 * 行為未定義，排出來的順序會隨引擎與輸入而變，而且不會報錯。
 */
function timeOf(value: string | null | undefined): number | null {
  if (!value) return null
  const t = Date.parse(value)
  return Number.isNaN(t) ? null : t
}

/** 名稱比較；平手再比 id，讓同名的兩個 Space 每次都排在同樣位置。 */
function byName(a: Space, b: Space): number {
  return collator.compare(a.displayName ?? '', b.displayName ?? '') || collator.compare(a.id, b.id)
}

/**
 * 依最後活動時間比。**沒有時間的一律排最後，不論方向**——
 * 「舊→新」時若把它們當成最舊排到最前面，清單開頭會是一整排「最後活動 —」，
 * 真正最舊的那些反而被擠到下面。沒有時間的彼此依名稱排，保持穩定。
 */
function byActivity(direction: 1 | -1) {
  return (a: Space, b: Space): number => {
    const ta = timeOf(a.lastActiveTime)
    const tb = timeOf(b.lastActiveTime)
    if (ta === null && tb === null) return byName(a, b)
    if (ta === null) return 1
    if (tb === null) return -1
    return (ta - tb) * direction || byName(a, b)
  }
}

// 類型的顯示順序：群組 → 私訊 → 多人對話 → 其他。與 spaceTypeLabel 的值域一致；
// 認不得的類型排最後但不丟掉（丟掉就是靜默少給資料）。
const TYPE_ORDER: Record<string, number> = {
  SPACE: 0,
  DIRECT_MESSAGE: 1,
  DM: 1,
  GROUP_CHAT: 2,
}

function typeRank(type: string | null | undefined): number {
  return TYPE_ORDER[type ?? ''] ?? 3
}

const newestFirst = byActivity(-1)

const SPACE_COMPARATORS: Record<SpaceSortKey, (a: Space, b: Space) => number> = {
  'activity-desc': newestFirst,
  'activity-asc': byActivity(1),
  name: byName,
  'name-desc': (a, b) => byName(b, a),
  // 同類型之內依最新活動：選「類型」的人是想分組看，組內仍以「最近在動的」最有用
  type: (a, b) => typeRank(a.type) - typeRank(b.type) || newestFirst(a, b),
  // ⚠️ 降冪只反轉「組的順序」，組內仍是新→舊。不能寫成 `type(b, a)`：那會連
  // 組內一起翻成舊→新，Viewer 點的是「類型」欄，沒有要求改變組內順序
  'type-desc': (a, b) => typeRank(b.type) - typeRank(a.type) || newestFirst(a, b),
}

/**
 * 排序 Space 清單。回傳新陣列，不改動傳入的陣列（它是 store 的 state）。
 *
 * `pinnedFirst` 開啟時先分成「釘選／未釘選」兩組，**各組內仍套用所選排序**；
 * 關閉時釘選與否完全不影響位置。
 */
export function sortSpaces(items: readonly Space[], options: SpaceSortOptions): Space[] {
  const compare = SPACE_COMPARATORS[options.sort] ?? SPACE_COMPARATORS[DEFAULT_SPACE_SORT.sort]
  const sorted = [...items].sort(compare)
  if (!options.pinnedFirst) return sorted
  return [...sorted.filter((s) => s.pinned), ...sorted.filter((s) => !s.pinned)]
}

/**
 * 同一時間的兩則**維持傳入的順序**（Array.sort 是穩定排序），不再以 id 決勝。
 *
 * 曾經用 id 決勝，結果把 MentionInbox 既有的三條鍵盤導航測試打紅：那些測試
 * 用同一個 create_time 造三則，依賴的正是「原本的順序不被打亂」——
 * 後端（repository.list_mentions 的 ORDER BY create_time DESC）與
 * selectMentionsByState 給的順序本來就確定，這裡沒有理由另立一套平手規則。
 */
function mentionByTime(direction: 1 | -1) {
  return (a: Mention, b: Mention): number => {
    const ta = timeOf(a.create_time)
    const tb = timeOf(b.create_time)
    // 沒有時間的一樣排最後，不論方向，理由同 byActivity
    if (ta === null && tb === null) return 0
    if (ta === null) return 1
    if (tb === null) return -1
    return (ta - tb) * direction
  }
}

const mentionNewestFirst = mentionByTime(-1)

const MENTION_COMPARATORS: Record<MentionSortKey, (a: Mention, b: Mention) => number> = {
  'time-desc': mentionNewestFirst,
  'time-asc': mentionByTime(1),
  // 依 Space／發言者：組內一律新→舊。會選這兩個的人是想「一個對話一個對話處理」，
  // 組內最新的那則通常就是要回的那則
  space: (a, b) => collator.compare(a.space_name ?? '', b.space_name ?? '') || mentionNewestFirst(a, b),
  sender: (a, b) =>
    collator.compare(a.sender_display ?? '', b.sender_display ?? '') || mentionNewestFirst(a, b),
}

/** 排序 Mention。回傳新陣列，不改動傳入的陣列。 */
export function sortMentions(items: readonly Mention[], sort: MentionSortKey): Mention[] {
  const compare = MENTION_COMPARATORS[sort] ?? MENTION_COMPARATORS[DEFAULT_MENTION_SORT]
  return [...items].sort(compare)
}

export interface ListSortPrefs {
  space: SpaceSortOptions
  mention: MentionSortKey
}

export const DEFAULT_LIST_SORT: ListSortPrefs = {
  space: DEFAULT_SPACE_SORT,
  mention: DEFAULT_MENTION_SORT,
}

/**
 * 把 localStorage 讀出來的字串還原成偏好。**逐欄驗證、認不得的欄位退回預設**。
 *
 * ⚠️ 不能直接 `JSON.parse(raw) as ListSortPrefs`：舊版本存的、手動改過的、
 * 或之後改名了的排序鍵會原樣流進 comparator 查表，查不到就是 undefined——
 * 那正是「讀不到時用預設值」要擋的情況，只是它不會丟例外，會讓畫面默默
 * 用錯的排序。一欄壞掉只退那一欄，另一欄 Viewer 選過的值保留。
 */
export function parseListSort(raw: string | null): ListSortPrefs {
  if (!raw) return DEFAULT_LIST_SORT
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    return DEFAULT_LIST_SORT
  }
  if (typeof data !== 'object' || data === null) return DEFAULT_LIST_SORT
  const obj = data as { space?: { sort?: unknown; pinnedFirst?: unknown }; mention?: unknown }
  const sort = obj.space?.sort
  const pinnedFirst = obj.space?.pinnedFirst
  return {
    space: {
      sort: SPACE_SORT_KEYS.includes(sort as SpaceSortKey)
        ? (sort as SpaceSortKey)
        : DEFAULT_SPACE_SORT.sort,
      pinnedFirst: typeof pinnedFirst === 'boolean' ? pinnedFirst : DEFAULT_SPACE_SORT.pinnedFirst,
    },
    mention: MENTION_SORT_KEYS.includes(obj.mention as MentionSortKey)
      ? (obj.mention as MentionSortKey)
      : DEFAULT_MENTION_SORT,
  }
}

export interface MentionTruncation {
  /** 已載入（前端手上）的筆數 */
  loaded: number
  /** 後端的總數（兩個分頁合計） */
  total: number
}

/**
 * 收件匣是不是被截斷了。沒截斷回 null。
 *
 * 🔴 收件匣**不是**完整清單：`GET /mentions` 預設只回 200 筆（server.py 的
 * `MENTIONS_PAGE_DEFAULT`），SQL 是 repository.list_mentions 的
 * `ORDER BY create_time DESC LIMIT ?`，兩種狀態合計。這是既有行為，不是排序
 * 帶進來的；但排序讓它變得會誤導——「舊→新」排的是最新 200 筆裡最舊的那些，
 * 不是真正最舊的，而畫面看不出來。這個 codebase 的硬規則是「靜默少給資料
 * 一律明示」，所以偵測到就在清單上方說出來。
 *
 * 判準：`counts` 來自 repository.count_mentions，算的是這位 Viewer 的**全部**
 * Mention（manual 併進 pending，與清單同一套歸類），所以
 * `pending + resolved > items.length` 就代表有東西沒載入。
 * 🔴 這個判準的前提是「清單與 counts 來自同一個快照」。2026-10-01 以前並非如此：
 * 後端先讀清單、補完內容（好幾秒）才讀 counts，採集器在中間寫入的新 Mention
 * 只算進 counts，於是 90 筆的清單配上 91 的總數，跳出假的截斷提示。現在兩者在
 * repository.list_mentions_with_counts 裡同一個讀取交易讀完；拆開它，這裡就會誤報。
 * ⚠️ 反方向不成立：count_mentions 會丟掉 pending／manual／resolved 以外的
 * 狀態，所以「沒偵測到」只是「沒有證據說被截斷」。
 *
 * 2026-09-30 實測（Viewer 本機 DB，唯讀 SELECT）：89 筆 resolved、0 筆 pending，
 * 未截斷。後端依排序方向查詢（或翻頁）屬未來工作，這次不改後端。
 */
export function mentionTruncation(
  counts: MentionCounts,
  loaded: number,
): MentionTruncation | null {
  const total = (counts.pending ?? 0) + (counts.resolved ?? 0)
  return total > loaded ? { loaded, total } : null
}
