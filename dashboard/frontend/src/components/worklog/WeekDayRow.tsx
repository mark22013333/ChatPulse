import { cn } from '@/lib/utils'
import { formatHours, weekDayStatus, type WorklogDayStatus } from '@/store/worklog'
import type { WorklogWeekDay } from '@/lib/types'

/**
 * 週欄裡的一天。
 *
 * 排版照證據欄（`evidence/EvidenceList.tsx`）那一套帳本式：三欄 grid、
 * 數字用 `.metric` 靠右、**七列共用同一條右緣**。那是整套設計最主要的識別
 * 特徵，所以這裡刻意用與證據欄相同的 grid template，而不是各畫各的。
 *
 * 它是**可點選的凹陷列**（`bg-background`），不是卡片——`VOCABULARY.md` §4：
 * 可勾選／可瀏覽的列用凹陷，卡片留給內容面板。
 */

/**
 * ISO 星期 → 中文。
 *
 * **一定要用對照表，不可以拿 `weekday` 去索引一個七元素陣列。** 後端回的是
 * ISO 星期（1＝一），但那個約定哪天變了（或者有人把它換成 ZPlanner 原生的
 * 英文縮寫），陣列索引會安靜地整排錯位——七列每一列都標錯星期，而日期是對的，
 * 光看畫面完全不會發現。對照表查不到就是查不到，出不了這種錯。
 */
const WEEKDAY_LABEL: Record<number, string> = {
  1: '週一',
  2: '週二',
  3: '週三',
  4: '週四',
  5: '週五',
  6: '週六',
  7: '週日',
}

/** 查不到就回空字串：寧可只顯示日期，也不要編一個星期出來。 */
export function weekdayLabel(weekday: number): string {
  return WEEKDAY_LABEL[weekday] ?? ''
}

/**
 * `2026-09-07` → `09/07`。
 *
 * 純字串切割，**不經過 `Date`**：`new Date('2026-09-07')` 會被當成 UTC 午夜，
 * 在 UTC+8 以外的時區印回來就變成前一天。
 */
export function formatMonthDay(iso: string): string {
  const parts = iso.split('-')
  return parts.length === 3 ? `${parts[1]}/${parts[2]}` : iso
}

/**
 * 狀態用**字符**表達，不用顏色（設計紅線：顏色是稀有資源）。
 *
 * ⚠️ **`absent` 是空字串，不是 `—`。** 規格同時說「未產生用 `—`」與「缺值顯示
 * `—`」，而這一列的缺值就在隔壁的時數欄——兩欄各放一個 `—` 會變成並排的兩條
 * 槓，讀起來像表格斷掉，而不是像「這天還沒交代」。那一列仍然有兩重編碼：
 * 時數欄的 `—` ＋ 點線規線，加上底下那行讀得出來的「未產生」。
 */
const STATUS_CHAR: Record<WorklogDayStatus, string> = {
  submitted: '✓',
  drafted: '▸',
  partial: '†',
  absent: '',
}

/**
 * 狀態的第二重編碼是**規線**（`VOCABULARY.md` §7：實線＝完整、虛線＝降級、
 * 點線＝缺值）。色盲條件下也分得出來，而且 1px 灰階就成立。
 *
 * 線型套在整個凹陷列的邊框上，不是只套底線：這一列本來就要有邊框才看得出
 * 是個可點的凹陷區塊（VOCABULARY §4），讓那條既有的邊框直接承載語意，
 * 比再疊一條底線乾淨。token 與證據欄的 `RULE` 保持一致。
 */
const STATUS_RULE: Record<WorklogDayStatus, string> = {
  submitted: 'border-line-evidence border-solid',
  drafted: 'border-line-evidence border-solid',
  partial: 'border-caution-line border-dashed',
  absent: 'border-line-evidence border-dotted',
}

/** 讀得出來的狀態說明。畫面上那個字符要有對應的文字，不能只有形狀。 */
const STATUS_TEXT: Record<WorklogDayStatus, string> = {
  submitted: '已送出',
  drafted: '有草稿未送',
  partial: '部分未完成',
  absent: '未產生',
}

export function WeekDayRow({
  day,
  selected,
  onSelect,
}: {
  day: WorklogWeekDay
  selected: boolean
  onSelect: (workDate: string) => void
}) {
  const status = weekDayStatus(day)
  const hours = formatHours(day.draft?.hours)
  const weekday = weekdayLabel(day.weekday)

  return (
    <button
      type="button"
      onClick={() => onSelect(day.work_date)}
      // aria-current="date" 而不是自己造一個 data 屬性：輔助技術認得它，
      // 而「現在在看哪一天」正是這一欄唯一的選取語意
      aria-current={selected ? 'date' : undefined}
      className={cn(
        'block w-full rounded-md border bg-background px-2 py-1.5 text-left transition-colors',
        STATUS_RULE[status],
        selected ? 'border-line-strong' : 'hover:border-line-strong',
      )}
    >
      {/* 三欄：星期與日期 / 時數（固定寬、右對齊）/ 狀態字符。
          欄寬與證據欄共用同一組 token，所以兩欄的數字對得起來。 */}
      <span className="grid grid-cols-[1fr_var(--container-metric)_1.75rem] items-baseline gap-x-2">
        <span className="min-w-0 truncate text-xs">
          <span className={cn(selected ? 'text-foreground' : 'text-fg-dim')}>{weekday}</span>
          <span className="metric text-fg-subtle ml-1.5 text-2xs">
            {formatMonthDay(day.work_date)}
          </span>
        </span>
        <span
          className={cn(
            'metric text-right text-sm',
            status === 'absent' ? 'text-fg-subtle' : 'text-foreground',
          )}
        >
          {hours}
        </span>
        <span className="text-fg-dim text-2xs">{STATUS_CHAR[status]}</span>
      </span>

      {/* 字符要配一句讀得出來的話。`—` 那一列也照樣寫「未產生」，
          不是留白——留白與「載入失敗」在畫面上長得一樣。 */}
      <span className="text-fg-subtle mt-0.5 block text-2xs">
        {STATUS_TEXT[status]}
        {day.draft && day.draft.unassigned_count > 0 ? (
          <>
            <span aria-hidden className="mx-1.5 inline-block h-2.5 w-px bg-line-strong align-middle" />
            {/* 未選專案擋住送出（ADR-0008），週欄要看得出「這天還差幾筆沒決定」 */}
            <span>還有 {day.draft.unassigned_count} 筆沒選專案</span>
          </>
        ) : null}
      </span>

      {/* ZPlanner 已經有工時的那天要講出來，否則「本地沒有草稿」會被讀成
          「這天什麼都沒有」，然後被重複填一次。這一行與上面的狀態是兩本帳，
          不相加——一個是已經進系統的，一個是還沒送出的。 */}
      {day.zplanner ? (
        <span className="text-fg-subtle mt-0.5 block text-2xs">
          ZPlanner 已填 <span className="metric">{formatHours(day.zplanner.hours)}</span> 小時
        </span>
      ) : null}
    </button>
  )
}
