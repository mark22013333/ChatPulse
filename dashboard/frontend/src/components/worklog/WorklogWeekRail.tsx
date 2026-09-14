import { useEffect, useRef } from 'react'
import { Button } from '@/components/ui/button'
import { WeekDayRow, formatMonthDay } from '@/components/worklog/WeekDayRow'
import { hashForWorklog } from '@/lib/route'
import { cn } from '@/lib/utils'
import { useRouter } from '@/router/useRouter'
import { formatHours, mondayOf, remainingHours, useWorklogStore } from '@/store/worklog'
import type { WorklogWeekResponse } from '@/lib/types'

/**
 * 工時草稿的週欄（左欄）。
 *
 * 這一欄回答「這一週哪幾天還沒交代」。七天各一列，狀態用字符與規線表達
 * （`✓` 已送出／`▸` 有草稿未送／`—` 未產生／`†` 部分失敗），小時數靠右
 * 共用同一條右緣——與證據欄同一套帳本式排版，不做卡片。
 *
 * ### 兩個會安靜出錯的地方
 *
 * 1. **顯示哪一週一律讀回應的 `week_start`／`week_end`**，不是我們送出去的
 *    參數。後端與 ZPlanner 都會正規化日期（傳週二回的是週一），拿參數當
 *    答案會顯示一個與內容不符的範圍，而且七列的日期是對的、只有標題錯。
 * 2. **ZPlanner 不可用不是載入失敗**。那時 `zplanner.available` 是 false、
 *    `days[].zplanner` 全是 null，但本地草稿照常回。這裡要把原因寫在畫面上
 *    ——靜默少掉一行，使用者會以為功能壞了。
 */
export function WorklogWeekRail({ active }: { active: boolean }) {
  const weekStart = useWorklogStore((state) => state.weekStart)
  const week = useWorklogStore((state) => state.week)
  const weekLoading = useWorklogStore((state) => state.weekLoading)
  const weekError = useWorklogStore((state) => state.weekError)
  const selectedDate = useWorklogStore((state) => state.selectedDate)
  const loadWeek = useWorklogStore((state) => state.loadWeek)
  const shiftWeek = useWorklogStore((state) => state.shiftWeek)
  const setWeekStart = useWorklogStore((state) => state.setWeekStart)

  // 點日列走 navigate 而不是直接 selectDate：「點擊」與「貼網址」走同一條
  // 路徑，只有一種行為要維護（同 `SpacesRail`）
  const { navigate } = useRouter()

  /**
   * 換週就重載，**但只在這個工作台真的在看的時候**。
   *
   * `Pane` 刻意不卸載子元件（三個工作台常駐掛載，見 `AppShell`），所以少了
   * `active` 這道閘，光是打開 app 就會對 ZPlanner 發一次請求——即使使用者
   * 從頭到尾沒點進工時。
   *
   * `loadedRef` 記的是「已經為哪一週發過請求」，用途是讓切走再切回來不要
   * 重打。**刻意不拿 `week.week_start` 來判斷**：那是後端正規化過的值，
   * 萬一它與送出的參數不相等，這個 effect 會變成無限重載的迴圈。
   * 重試走的是按鈕直接呼叫 `loadWeek()`，不經過這裡，所以不受影響。
   */
  const loadedRef = useRef<string | null>(null)
  useEffect(() => {
    if (!active) return
    if (loadedRef.current === weekStart) return
    loadedRef.current = weekStart
    void loadWeek(weekStart)
  }, [active, weekStart, loadWeek])

  const thisWeek = mondayOf()

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-line-strong flex shrink-0 items-center gap-1 border-b px-gutter-tight py-2">
        <Button size="icon-xs" variant="ghost" aria-label="上一週" onClick={() => shiftWeek(-1)}>
          ◂
        </Button>
        {/* 標題就是這一週的範圍。載入完成前不顯示任何日期——顯示一個「我們
            猜的」範圍，比空著更難發現它與內容不符。 */}
        <h2 className="metric flex-1 text-center text-xs font-semibold">
          {week ? `${formatMonthDay(week.week_start)}–${formatMonthDay(week.week_end)}` : '　'}
        </h2>
        <Button size="icon-xs" variant="ghost" aria-label="下一週" onClick={() => shiftWeek(1)}>
          ▸
        </Button>
      </div>

      {weekStart !== thisWeek ? (
        <div className="shrink-0 border-b border-line px-gutter-tight py-1.5">
          <Button size="xs" variant="ghost" onClick={() => setWeekStart(thisWeek)}>
            回到本週
          </Button>
        </div>
      ) : null}

      {weekError ? (
        <div
          role="alert"
          className="shrink-0 border-b border-line px-gutter-tight py-2 text-fg-dim text-xs leading-relaxed"
        >
          <p>{weekError}</p>
          <Button size="xs" variant="outline" className="mt-1.5" onClick={() => void loadWeek()}>
            重試
          </Button>
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto px-gutter-tight py-2">
        {week ? (
          <>
            <WeekTotals week={week} />
            <ul className="mt-2 space-y-1">
              {week.days.map((day) => (
                <li key={day.work_date}>
                  <WeekDayRow
                    day={day}
                    selected={day.work_date === selectedDate}
                    onSelect={(workDate) => navigate(hashForWorklog(workDate))}
                  />
                </li>
              ))}
            </ul>
          </>
        ) : weekLoading ? (
          <p className="text-fg-dim text-xs">載入中…</p>
        ) : weekError ? null : (
          <p className="text-fg-dim text-xs leading-relaxed">還沒有這一週的資料。</p>
        )}
      </div>
    </div>
  )
}

/**
 * 週合計。
 *
 * 用 `.metric text-sm` 而**不是** `metric-lg`：全站最大的那個字只有一個名額
 * （`VOCABULARY.md` §8），而它已經留給送出確認框裡的「實際會送出幾小時」
 * ——那是不可逆操作的最後一個數字。週欄這個數字是用來定位「這週進度如何」，
 * 值得對齊、不值得最大。
 */
function WeekTotals({ week }: { week: WorklogWeekResponse }) {
  const { totals, zplanner } = week

  return (
    <dl className="text-xs">
      <TotalRow label="本週草稿" value={formatHours(totals.draft_hours)} unit="h" />
      <TotalRow
        label="已送出"
        value={`${totals.days_submitted} / ${week.days.length}`}
        unit="天"
      />
      {totals.entries_unassigned > 0 ? (
        // 未選專案擋住送出（ADR-0008）。這個數字是「這週還差幾個決定」，
        // 不是統計數字，所以 0 的時候整列收起來，不留一個永遠是 0 的欄位
        <TotalRow
          label="未選專案"
          value={String(totals.entries_unassigned)}
          unit="筆"
          rule="border-caution-line border-dashed"
        />
      ) : null}

      {zplanner.available ? (
        <>
          <TotalRow label="已填 ZPlanner" value={formatHours(zplanner.total_hours)} unit="h" />
          {/* 目標與「還差」只在 ZPlanner 真的給了目標時才畫。後端取不到就
              整個不回這個欄位（不是 null），所以這裡用存在性判斷——
              寫成 `?? 40` 會憑空生出一個目標，然後讓下面那個數字變成假的。 */}
          {zplanner.target_hours ? (
            <>
              <TotalRow label="本週目標" value={formatHours(zplanner.target_hours)} unit="h" />
              <TotalRow
                label="還差"
                value={remainingHours(zplanner.target_hours, zplanner.total_hours)}
                unit="h"
              />
            </>
          ) : null}
        </>
      ) : (
        <div className="border-line-evidence border-b border-dotted py-1.5">
          <div className="grid grid-cols-[1fr_var(--container-metric)_1.75rem] items-baseline gap-x-2">
            <dt className="text-fg-dim min-w-0 truncate">已填 ZPlanner</dt>
            <dd className="col-span-2 grid grid-cols-subgrid items-baseline">
              <span className="text-fg-subtle col-span-2 text-right text-sm">—</span>
            </dd>
          </div>
          {/* 🔴 不可以靜默隱藏：沒有這句話，使用者只會看到少了一行，
              然後以為整個功能壞了。reason 是後端給的繁中句子，直接顯示。 */}
          <p className="text-fg-subtle mt-0.5 text-2xs leading-relaxed">
            {zplanner.reason}　本地草稿不受影響，照常顯示。
          </p>
        </div>
      )}
    </dl>
  )
}

/** 帳本式的一列：標籤 / 數字（固定寬、右對齊）/ 單位。右緣與七天列共用。 */
function TotalRow({
  label,
  value,
  unit,
  rule = 'border-line-evidence border-solid',
}: {
  label: string
  value: string
  unit: string
  rule?: string
}) {
  return (
    <div
      className={cn(
        'grid grid-cols-[1fr_var(--container-metric)_1.75rem] items-baseline gap-x-2 border-b py-1.5',
        rule,
      )}
    >
      <dt className="text-fg-dim min-w-0 truncate">{label}</dt>
      <dd className="col-span-2 grid grid-cols-subgrid items-baseline">
        <span className="metric text-foreground text-right text-sm">{value}</span>
        <span className="text-fg-subtle text-2xs">{unit}</span>
      </dd>
    </div>
  )
}
