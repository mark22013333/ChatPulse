import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import type { WorklogWeekDay, WorklogWeekResponse } from '@/lib/types'

/**
 * 工時草稿的前端狀態。
 *
 * ### 為什麼不叫 `draft`
 *
 * `store/draft.ts` 與 `components/draft/` 已經是 **Draft Reply** 的地盤。兩者
 * 中文都叫「草稿」，但寫回去的地方完全不同：一個送進 Google Chat 的討論串，
 * 一個寫進 ZPlanner 的工時、會進月結、事後要改得走 correction-request。
 * 共用一個名字遲早會有人 import 錯，而且錯了不會編譯失敗。
 *
 * ### 目前的範圍
 *
 * 只有「在看哪一天」＋**週檢視**（`GET /worklogs/week`）。
 *
 * 單日的草稿產生（SSE）、逐筆存檔（PUT）與送出**不在這裡**，在
 * `store/worklogDay.ts`（D-15）：那些狀態每秒都在變，擴進來會讓週欄的
 * selector 跟著重畫、兩邊欄位互相引用。依賴是**單向**的——worklogDay 寫入
 * 成功後呼叫這裡的 `loadWeek()` 刷新週欄，這個檔永遠不 import worklogDay。
 * （原本預想的 `days: Record<ISODate, DayDraft>` 多天並存已否決：Viewer 一次
 * 只看一天，換日即中止串流，多天並存只會多出「哪一天在串流」要判斷。）
 */

/**
 * `Date` → `YYYY-MM-DD`，**用本地時區的年月日**。
 *
 * 不可以用 `toISOString().slice(0, 10)`：那是 UTC，台北時間每天早上八點以前
 * 都會少一天。而日期在這個功能裡決定工時寫到哪一天，錯一天要走
 * correction-request 才改得回來。
 */
export function isoDate(date: Date): string {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

/**
 * 某一天所在那一週的星期一（`YYYY-MM-DD`）。
 *
 * ⚠️ `Date.getDay()` 的**星期日是 0**，不是 7。直接拿它減會讓星期日跳到
 * 「下一週的週一」——整欄七天全部錯位，而且只在一週七天裡的一天出錯，
 * 用一次很難發現。所以先換算成 ISO 星期（1＝一…7＝日）再減。
 */
export function mondayOf(date: Date = new Date()): string {
  // 砍掉時分秒，避免夏令時間或跨日邊界讓 setDate 多跳一天
  const d = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  const isoWeekday = d.getDay() === 0 ? 7 : d.getDay()
  d.setDate(d.getDate() - (isoWeekday - 1))
  return isoDate(d)
}

/**
 * `YYYY-MM-DD` ＋ n 天。
 *
 * 用 `Date` 的月底進位而不是自己算，順便讓跨月、跨年、閏年都是免費的。
 * 解析時**不走 `new Date('2026-09-07')`**：那個字串會被當成 UTC 午夜，
 * 在 UTC+8 以外的時區印回來就變成前一天。
 */
export function addDays(iso: string, days: number): string {
  const [y, m, d] = iso.split('-').map(Number)
  const dt = new Date(y, m - 1, d)
  dt.setDate(dt.getDate() + days)
  return isoDate(dt)
}

/**
 * 時數字串 → 顯示用的一位小數。
 *
 * 後端一律回 `"8.00"` 這種兩位小數字串（見 `WorklogWeekDraft.hours` 的註解）；
 * 分配演算法保證是 0.5 的倍數，所以砍成一位小數是無損的。
 *
 * **null／空字串／算不動的值回 `—`，不回 `0.0`**：「這天沒有草稿」與「這天
 * 填了零小時」是兩件不同的事，寫成同一個數字會讓人以為自己真的填過了。
 */
export function formatHours(raw: string | null | undefined): string {
  if (raw === null || raw === undefined) return '—'
  if (String(raw).trim() === '') return '—'
  const value = Number.parseFloat(raw)
  if (!Number.isFinite(value)) return '—'
  return value.toFixed(1)
}

/**
 * 這一週還差幾小時（目標 − 已填）。
 *
 * **只做減法，不自己算完成率。** 目標時數會因人、因請假而不同，那是 ZPlanner
 * 的業務規則；我們自己除出來的百分比遲早會與它網頁上的數字對不起來。減法則是
 * 安全的——兩個數字都是 ZPlanner 給的。
 *
 * 已經超過目標時回 `0.0` 而不是負數：「還差 −3.0 小時」要讓人在腦子裡轉一次
 * 才看得懂，而這一欄的用途正是「還要補多少」。
 *
 * 任何一邊算不出數字就回 `—`，與 `formatHours` 一致。
 */
export function remainingHours(
  target: string | null | undefined,
  filled: string | null | undefined,
): string {
  const t = Number.parseFloat(String(target ?? ''))
  const f = Number.parseFloat(String(filled ?? ''))
  if (!Number.isFinite(t) || !Number.isFinite(f)) return '—'
  return Math.max(0, t - f).toFixed(1)
}

/** 一天在週欄裡的狀態。`absent`＝還沒產草稿（不是錯誤，是多數日子的常態）。 */
export type WorklogDayStatus = 'submitted' | 'partial' | 'drafted' | 'absent'

/**
 * 一天的草稿狀態 → 週欄要畫哪一種。
 *
 * **判斷順序有意義**：`partial` 排在 `submitted` 前面。一天裡有幾筆送成功、
 * 有幾筆沒有的時候，把它畫成「已送出」會讓人以為整天都交代完了，而實際上
 * 還有幾筆卡著——那幾筆永遠不會有人回來補。
 *
 * `unknown_count`（送出時逾時）也算 `partial`：**逾時不保證沒寫進去**，
 * 它需要人去對帳，不能當成沒事。
 *
 * 刻意不寫成 exhaustive switch：`status` 是後端字串，多一個值時這裡應該
 * 安全地退回 `drafted`（看得到、可以點進去），而不是編譯失敗或畫成空白。
 */
export function weekDayStatus(day: WorklogWeekDay): WorklogDayStatus {
  const draft = day.draft
  if (!draft) return 'absent'
  if (draft.status === 'partial') return 'partial'
  if (draft.failed_count > 0 || draft.unknown_count > 0) return 'partial'
  if (draft.status === 'submitted') return 'submitted'
  return 'drafted'
}

interface WorklogState {
  /** `YYYY-MM-DD`。`null` ＝週檢視，沒有指定哪一天（不是「今天」） */
  selectedDate: string | null

  /**
   * 送給 `GET /worklogs/week` 的 `week_start` 參數。
   *
   * ⚠️ **這不是「畫面上那一週」。** 顯示哪一週一律讀 `week.week_start`
   * ／`week.week_end`——後端（與 ZPlanner）會把日期正規化，傳週二進去回的
   * 是週一。拿這個值去畫標題，遲早會顯示一個與內容不符的日期範圍。
   */
  weekStart: string
  week: WorklogWeekResponse | null
  weekLoading: boolean
  weekError: string | null

  selectDate: (date: string | null) => void
  /** 換一週（只改參數；實際載入由畫面的 effect 觸發，見 `WorklogTable`）。 */
  setWeekStart: (weekStart: string) => void
  /** 前後移動 n 週。`-1` 是上一週。 */
  shiftWeek: (deltaWeeks: number) => void
  /** 省略 `weekStart` ＝ 重載目前這一週。 */
  loadWeek: (weekStart?: string) => Promise<void>
  reset: () => void
}

export const useWorklogStore = create<WorklogState>((set, get) => ({
  selectedDate: null,
  weekStart: mondayOf(),
  week: null,
  weekLoading: false,
  weekError: null,

  selectDate: (selectedDate) => set({ selectedDate }),
  setWeekStart: (weekStart) => set({ weekStart }),
  shiftWeek: (deltaWeeks) => set({ weekStart: addDays(get().weekStart, deltaWeeks * 7) }),

  loadWeek: async (weekStart) => {
    const target = weekStart ?? get().weekStart
    set({ weekLoading: true, weekError: null })
    try {
      const week = await api.worklogWeek(target)
      // 連按「上一週」時回應可能亂序抵達。**只採用還停在同一週的那一份**，
      // 否則畫面會停在使用者已經離開的那一週，而且完全看不出哪裡不對。
      if (get().weekStart !== target) return
      set({ week })
    } catch (err) {
      if (get().weekStart !== target) return
      set({ weekError: errorMessage(err) })
    } finally {
      if (get().weekStart === target) set({ weekLoading: false })
    }
  },

  reset: () =>
    set({
      selectedDate: null,
      weekStart: mondayOf(),
      week: null,
      weekLoading: false,
      weekError: null,
    }),
}))
