import type { SortState } from '@/lib/paginate'
import type { WorklogWeekDay } from '@/lib/types'

/**
 * 工時草稿表格要用的純函式（設計規格 2026-10-01 §4.2、§4.4）。
 *
 * 不碰 store、不碰 DOM、不碰網路：tabs 怎麼判、篩選與排序怎麼做、「記到哪張單」欄
 * 怎麼寫、比例條怎麼算，全在這裡測。元件只負責把它們接起來。
 *
 * ⚠️ 一天是什麼狀態（未產生／草稿／部分未完成／已送出）的判準**不在這裡**，
 * 在 `store/worklog.ts` 的 `weekDayStatus`——舊的週欄一直用那一支，這裡不另寫
 * 第二份定義（兩份遲早會漂移，然後表格與週欄對同一天講兩種話）。用參數傳入
 * 而不是 import：`lib/` 底下的非測試檔一律不 import store（同 `lib/spaceTable.ts`）。
 *
 * ⚠️ 這支**不切頁**。切頁一律交給 `lib/paginate.ts`，這裡的 filter 與 sort
 * 會先對全部列跑完，paginate 才切頁。
 */

/** 與 `store/worklog.ts` 的 `WorklogDayStatus` 同一組值；在這裡重述型別，理由見檔頭。 */
export type WorklogRowStatus = 'submitted' | 'partial' | 'drafted' | 'absent'
export type StatusOf = (day: WorklogWeekDay) => WorklogRowStatus

// ── 模組內 tabs（子視圖，不是模組切換；規格 §1） ────────────────────────

export const WORKLOG_TABS = ['this', 'last', 'unsent'] as const
export type WorklogTab = (typeof WORKLOG_TABS)[number]

export const WORKLOG_TAB_LABELS: Record<WorklogTab, string> = {
  this: '本週',
  last: '上週',
  unsent: '未送出',
}

/** Base UI Tabs 給的值是 unknown；認不得就回 null，由呼叫端決定不動。 */
export function parseWorklogTab(raw: unknown): WorklogTab | null {
  return WORKLOG_TABS.includes(raw as WorklogTab) ? (raw as WorklogTab) : null
}

/**
 * 表格在看什麼：**某一週**（週次由篩選列的翻週控制，可以翻到任何一週），
 * 或**未送出**（本週＋上週，見下）。
 *
 * ### 為什麼 tabs 是「捷徑」，不是獨立的狀態
 *
 * 規格 §1 的 tabs 是本週／上週／未送出，而 Viewer 又需要翻到更早的週次回頭
 * 補填（舊週欄就能翻到任何一週）。兩者並存時，若 tab 是一份獨立狀態，就會出現
 * 「tab 亮著『本週』、表格卻是三週前」這種自相矛盾的畫面。所以「本週」「上週」
 * 只是把週次設成那一週的**捷徑**，亮哪一顆由目前的週次**反推**
 * （`activeWorklogTab`）；翻到更早的週時兩顆都不亮，畫面上只有一個答案。
 * 被否決的做法：tabs 只留「本週／未送出」、週次全交給篩選列——那樣「本週」在
 * 翻到別週之後仍然亮著，是同一個矛盾。
 */
export type WorklogMode = 'week' | 'unsent'

/**
 * 目前該亮哪一顆 tab；翻到本週與上週以外的週時回 `null`（一顆都不亮）。
 *
 * 比較的是**送出去的週參數**（store 的 `weekStart`），不是回應的 `week_start`：
 * 回應還沒回來時 tab 也要馬上亮對。回應與參數對不上時表格另外會擋下來不畫。
 */
export function activeWorklogTab(
  mode: WorklogMode,
  weekStart: string,
  thisWeek: string,
  lastWeek: string,
): WorklogTab | null {
  if (mode === 'unsent') return 'unsent'
  if (weekStart === thisWeek) return 'this'
  if (weekStart === lastWeek) return 'last'
  return null
}

/**
 * 「未送出」要向 `GET /worklogs/week` 要哪幾週。
 *
 * ⚠️ **API 一次只給一週**，沒有「列出所有未送出的草稿」的端點。所以「未送出」
 * 的範圍只能是「本週＋上週」這兩週（兩次請求），**更早的未送出草稿不在裡面**
 * ——畫面上要把範圍寫出來，不然 Viewer 會以為「未送出」就是全部；更早的週
 * 要用翻週去看。
 * 被否決的做法：往回掃 N 週直到沒有草稿為止。每一週都是一次 ZPlanner 請求，
 * 而「沒有草稿」的那一週之前仍然可能有，停在哪裡都是猜。
 */
export function unsentWeeks(thisWeek: string, lastWeek: string): [string, string] {
  return [lastWeek, thisWeek]
}

/**
 * 「未送出」的判準：**有草稿、但還沒整天送完**（草稿＋部分未完成）。
 *
 * 不含「未產生」：那天還沒有任何東西可以送，放進來會讓每一個週末都變成
 * 「未送出」，真正卡著的那幾天反而被淹掉。要看還沒產生的天，用狀態篩選。
 * `partial` 一定要在裡面：有幾筆送失敗或逾時的那天最需要人回來處理。
 */
export function isUnsent(status: WorklogRowStatus): boolean {
  return status === 'drafted' || status === 'partial'
}

// ── 狀態篩選 ──────────────────────────────────────────────────────────

export const WORKLOG_STATUS_FILTERS = ['all', 'absent', 'drafted', 'partial', 'submitted'] as const
export type WorklogStatusFilter = (typeof WORKLOG_STATUS_FILTERS)[number]

/**
 * 狀態的文案。規格 §4.4 只寫了三種（未產生／草稿／已送出），但後端有第四種
 * `partial`（`lib/types.ts` 的 `WorklogDraftStatus`：有幾筆送成功、有幾筆沒有），
 * 而 `weekDayStatus` 還把「有筆數送出失敗或逾時」也歸進來。🔴 **不可以把它併進
 * 「草稿」或「已送出」**：併進已送出，那幾筆永遠不會有人回來補；併進草稿，
 * Viewer 會以為整天都還沒送，再按一次就把已經成功的那幾筆重複填進 ZPlanner。
 */
export const WORKLOG_STATUS_LABELS: Record<WorklogRowStatus, string> = {
  absent: '未產生',
  drafted: '草稿',
  partial: '部分未完成',
  submitted: '已送出',
}

export const WORKLOG_STATUS_FILTER_LABELS: Record<WorklogStatusFilter, string> = {
  all: '全部狀態',
  ...WORKLOG_STATUS_LABELS,
}

export function parseWorklogStatusFilter(raw: unknown): WorklogStatusFilter | null {
  return WORKLOG_STATUS_FILTERS.includes(raw as WorklogStatusFilter)
    ? (raw as WorklogStatusFilter)
    : null
}

export interface WorklogTableFilters {
  mode: WorklogMode
  status: WorklogStatusFilter
}

export const DEFAULT_WORKLOG_TABLE_FILTERS: WorklogTableFilters = { mode: 'week', status: 'all' }

/** 「這一天要不要留下」的判斷式，給 `paginate` 的 `filter` 用。 */
export function worklogTableFilter(
  filters: WorklogTableFilters,
  statusOf: StatusOf,
): (day: WorklogWeekDay) => boolean {
  return (day) => {
    const status = statusOf(day)
    if (filters.mode === 'unsent' && !isUnsent(status)) return false
    if (filters.status !== 'all' && status !== filters.status) return false
    return true
  }
}

// ── 排序 ──────────────────────────────────────────────────────────────

export type WorklogColumnKey = 'date' | 'hours'

export const DEFAULT_WORKLOG_SORT: SortState<WorklogColumnKey> = { key: 'date', direction: 'asc' }

/** 表頭第一次點下去的方向：日期照週序（舊→新），時數大→小（會點它就是想找最多的那天）。 */
export const WORKLOG_SORT_INITIAL: Record<WorklogColumnKey, 'asc' | 'desc'> = {
  date: 'asc',
  hours: 'desc',
}

/**
 * 草稿時數 → 可比較的數字。**沒有草稿、或時數算不動的，回 `null`**，
 * 由排序把它們一律放到最後——不當成 0：「沒有草稿」與「填了零小時」是兩件事
 * （`store/worklog.ts` 的 `formatHours` 同一條規則）。
 */
export function draftHoursOf(day: WorklogWeekDay): number | null {
  const raw = day.draft?.hours
  if (raw === null || raw === undefined || String(raw).trim() === '') return null
  const value = Number.parseFloat(raw)
  return Number.isFinite(value) ? value : null
}

/**
 * 對**全部**列排序（回新陣列）。日期是 `YYYY-MM-DD`，字串比較就是時間順序。
 *
 * 時數排序時，沒有數字的天**不論升降一律排最後**，同時數依日期排——否則
 * 升冪時七個「未產生」會全擠在最前面，真正有草稿的那幾天被推到第二頁。
 */
export function sortWorklogRows(
  rows: readonly WorklogWeekDay[],
  sort: SortState<WorklogColumnKey>,
): WorklogWeekDay[] {
  const byDate = (a: WorklogWeekDay, b: WorklogWeekDay) =>
    a.work_date < b.work_date ? -1 : a.work_date > b.work_date ? 1 : 0
  const sign = sort.direction === 'asc' ? 1 : -1
  return [...rows].sort((a, b) => {
    if (sort.key === 'date') return sign * byDate(a, b)
    const ha = draftHoursOf(a)
    const hb = draftHoursOf(b)
    if (ha === null && hb === null) return byDate(a, b)
    if (ha === null) return 1
    if (hb === null) return -1
    if (ha !== hb) return sign * (ha - hb)
    return byDate(a, b)
  })
}

// ── 草稿時數的比例條 ───────────────────────────────────────────────────

/**
 * 比例條的長度（0～1）＝這天的草稿時數 ÷ **表上時數最多的那天**。
 *
 * ⚠️ 分母刻意不是「8 小時」或 ZPlanner 的目標：目標因人、因請假而不同，那是
 * ZPlanner 的業務規則（`store/worklog.ts` 的 `remainingHours` 註解），我們自己
 * 定一個 8 會讓比例條看起來像「完成率」，而那是編出來的。比例條只回答
 * 「這幾天之間誰多誰少」。
 *
 * `max` 由呼叫端用**篩選前的全部列**算好傳進來，篩選時條子才不會跟著伸縮。
 */
export function hoursRatio(hours: number | null, max: number): number {
  if (hours === null || !(max > 0) || !(hours > 0)) return 0
  return Math.min(1, hours / max)
}

export function maxDraftHours(rows: readonly WorklogWeekDay[]): number {
  let max = 0
  for (const row of rows) {
    const h = draftHoursOf(row)
    if (h !== null && h > max) max = h
  }
  return max
}

// ── 「記到哪張單」欄 ────────────────────────────────────────────────────────

export type AssignmentSummary =
  | { kind: 'none' }
  | { kind: 'unassigned'; unassigned: number }
  | { kind: 'mixed'; assigned: number; unassigned: number }
  | { kind: 'assigned'; assigned: number }
  | { kind: 'invalid' }

/**
 * 「記到哪張單」欄要寫什麼。
 *
 * 🔴 **只講 Viewer 已經選了什麼，不講系統建議什麼**（ADR-0008：產生時歸屬一律
 * 留空，選擇權在 Viewer）。週 API 的 `draft` 摘要裡可能帶著 `generation_meta`
 * ——那是產生當下的分群快照，裡面有每一群的**候選**專案——這裡**一個字都不讀**。
 * 把候選顯示在「記到哪張單」這一欄，等於替 Viewer 做了選擇：表格一眼看過去像是
 * 已經分好了，而月結後要改得走 correction-request。
 *
 * 🔴 資料來源只有兩個計數：`entry_count`（這天幾段）與 `unassigned_count`
 * （還沒選專案的段數），已選＝兩者相減。**週 API 沒有各段選了哪個專案的名稱**，
 * 所以這一欄只能寫段數，寫不出「訂閱系統 5.0」這種內容（草圖上那樣寫，但前端
 * 拿不到；要的話得在後端的週摘要加欄位）。
 *
 * 認不出來的計數（負數、小數、未選多於總數）回 `invalid`，畫面照實寫「無法判讀」，
 * **不夾成合理的值**：夾成 0 會把「還有幾段沒選」講成「都選好了」。
 */
export function assignmentOf(day: WorklogWeekDay): AssignmentSummary {
  const draft = day.draft
  if (!draft) return { kind: 'none' }
  const total = draft.entry_count
  const unassigned = draft.unassigned_count
  const countOk = (n: unknown) => typeof n === 'number' && Number.isInteger(n) && n >= 0
  if (!countOk(total) || !countOk(unassigned) || unassigned > total) return { kind: 'invalid' }
  if (total === 0) return { kind: 'none' }
  if (unassigned === total) return { kind: 'unassigned', unassigned }
  if (unassigned === 0) return { kind: 'assigned', assigned: total }
  return { kind: 'mixed', assigned: total - unassigned, unassigned }
}

/** ADR-0008 的固定文案：還沒選的段落一律這樣寫，不寫任何專案名稱。 */
export const UNASSIGNED_LABEL = '歸屬尚未選擇'

export function assignmentLabel(summary: AssignmentSummary): string {
  switch (summary.kind) {
    case 'none':
      return '—'
    case 'unassigned':
      return `${UNASSIGNED_LABEL}（${summary.unassigned} 段）`
    case 'mixed':
      return `已選 ${summary.assigned} 段・${UNASSIGNED_LABEL} ${summary.unassigned} 段`
    case 'assigned':
      return `已選 ${summary.assigned} 段`
    case 'invalid':
      return '歸屬筆數無法判讀'
    default: {
      const exhaustive: never = summary
      void exhaustive
      return '歸屬筆數無法判讀'
    }
  }
}
