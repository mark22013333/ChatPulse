import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { DataTable, type DataTableColumn } from '@/components/table/DataTable'
import { Pager, TableTotal } from '@/components/table/Pager'
import { usePagedRows } from '@/components/table/usePagedRows'
import { api, errorMessage } from '@/lib/api'
import { formatMonthDay, weekdayLabel } from '@/lib/format'
import { toggleSort, type SortState } from '@/lib/paginate'
import { hashForWorklog } from '@/lib/route'
import {
  DEFAULT_WORKLOG_SORT,
  DEFAULT_WORKLOG_TABLE_FILTERS,
  WORKLOG_SORT_INITIAL,
  WORKLOG_STATUS_FILTERS,
  WORKLOG_STATUS_FILTER_LABELS,
  WORKLOG_STATUS_LABELS,
  WORKLOG_TABS,
  WORKLOG_TAB_LABELS,
  assignmentLabel,
  assignmentOf,
  draftHoursOf,
  hoursRatio,
  maxDraftHours,
  parseWorklogStatusFilter,
  activeWorklogTab,
  parseWorklogTab,
  unsentWeeks,
  sortWorklogRows,
  worklogTableFilter,
  type WorklogColumnKey,
  type WorklogRowStatus,
  type WorklogTab,
  type WorklogTableFilters,
} from '@/lib/worklogTable'
import { cn } from '@/lib/utils'
import { useRouter } from '@/router/useRouter'
import { addDays, formatHours, mondayOf, remainingHours, useWorklogStore, weekDayStatus } from '@/store/worklog'
import type { WorklogWeekDay, WorklogWeekResponse } from '@/lib/types'

const STATUS_ITEMS = WORKLOG_STATUS_FILTERS.map((value) => ({
  value,
  label: WORKLOG_STATUS_FILTER_LABELS[value],
}))

/**
 * 燈號。**燈號只是狀態的第二重編碼**（顏色＋讀得出來的文字），不是另一份資料：
 * 週 API 沒有任何「狀態以外」的健康度可以亮燈，自己另訂一套判準（例如「超過
 * 8 小時亮紅」）就是在編資料。語意色照規格 §2：完成／待確認／失敗／尚未開始
 * 各一個 token，不與 accent 藍共用。
 *
 * 文字一定要有（sr-only）：只有顏色的圓點，色盲與讀屏都讀不到。
 */
const LAMP: Record<WorklogRowStatus, { className: string; label: string }> = {
  submitted: { className: 'bg-verified', label: '完整' },
  drafted: { className: 'bg-caution', label: '待確認' },
  partial: { className: 'bg-destructive', label: '有段落送出失敗或逾時' },
  absent: { className: 'bg-idle', label: '尚未開始' },
}

const PILL: Record<WorklogRowStatus, string> = {
  submitted: 'border-line text-verified',
  drafted: 'border-caution-line text-caution',
  partial: 'border-destructive/40 text-destructive',
  absent: 'border-line text-fg-dim',
}

/**
 * 「未送出」tab 另外要的那一週（上週）。本週那一份仍然走 store，理由見元件註解。
 */
interface ExtraWeek {
  weekStart: string
  week: WorklogWeekResponse | null
  error: string | null
  loading: boolean
}

/**
 * 工時草稿的整頁表格（設計規格 2026-10-01 §4.4）：tabs → 篩選列 → 表格 → 分頁器。
 * 一列一天，點一列整頁切到 `#/worklog/:date` 的單日工作區。
 *
 * 取代舊的週欄（`WorklogWeekRail`，已移除）。篩選列上的翻週（◂ ▸、回到本週）
 * 沿用舊週欄的行為，可以翻到任何一週；tabs 的「本週」「上週」只是其中兩週的捷徑
 * （理由見 `lib/worklogTable.ts` 的 `WorklogMode`）。
 *
 * ### 🔴 `active` 閘門：沒在看工時就不發請求
 *
 * `Pane` 刻意不卸載子元件（三個工作台常駐掛載，見 `AppShell`），所以少了這道閘，
 * 光是打開 app 就會對 ZPlanner 發一次請求——即使 Viewer 從頭到尾沒點進工時。
 * 這裡**每一個**會打 API 的 effect 都先看 `active`（本週／上週走 store 的
 * `loadWeek`，「未送出」多打的上週也一樣），`WorklogTable.test.tsx` 與
 * `AppShell.test.tsx` 各有一條守著。
 *
 * ### 🔴 收起來時不卸載
 *
 * tabs、篩選、排序、頁碼都是這裡的元件 state；返回清單時它們還在，靠的是
 * AppShell 用 inert ＋ 透明把它移出版面流、不是條件渲染（`AppShell.test.tsx` 守著）。
 *
 * ### 為什麼本週／上週走 store，「未送出」多打的那一週走元件自己
 *
 * 單日工作區每次存檔、送出之後會呼叫 store 的 `loadWeek()` 刷新（`store/worklogDay.ts`），
 * 表格要跟著看到最新狀態，所以主要那一週一定要是 store 那一份。store 一次只放
 * 一週，「未送出」要的第二週（上週）就在這裡另外打；store 那一份一刷新
 * （`week` 換了參考）就跟著重打，才不會出現「本週那幾列更新了、上週那幾列還是舊的」。
 *
 * ### 欄位只放前端拿得到的
 *
 * 專案分配只寫段數、不寫專案名稱——週 API 沒有名稱（見 `lib/worklogTable.ts` 的
 * `assignmentOf`）；🔴 更不寫系統建議的候選專案（ADR-0008）。
 */
export function WorklogTable({ active, thisWeek: thisWeekProp }: { active: boolean; thisWeek?: string }) {
  const weekStart = useWorklogStore((state) => state.weekStart)
  const week = useWorklogStore((state) => state.week)
  const weekLoading = useWorklogStore((state) => state.weekLoading)
  const weekError = useWorklogStore((state) => state.weekError)
  const loadWeek = useWorklogStore((state) => state.loadWeek)
  const setWeekStart = useWorklogStore((state) => state.setWeekStart)
  const shiftWeek = useWorklogStore((state) => state.shiftWeek)

  // 點列走 navigate 而不是直接 selectDate：「點擊」與「貼網址」走同一條路徑
  const { navigate } = useRouter()

  // 「本週」以瀏覽器本地時區的星期一為準（`mondayOf` 的註解記了星期日的坑）。
  // 可注入只是為了測試不必跟著真實時鐘跑
  const thisWeek = thisWeekProp ?? mondayOf()
  const lastWeek = addDays(thisWeek, -7)

  const [filters, setFilters] = useState<WorklogTableFilters>(DEFAULT_WORKLOG_TABLE_FILTERS)
  const [sort, setSort] = useState<SortState<WorklogColumnKey>>(DEFAULT_WORKLOG_SORT)
  const patchFilters = (patch: Partial<WorklogTableFilters>) =>
    setFilters((current) => ({ ...current, ...patch }))

  /**
   * 週次的唯一真相是 store 的 `weekStart`（送給 API 的參數）——翻週、tab 捷徑、
   * 「回到本週」都只改它，與舊週欄同一套（`shiftWeek`／`setWeekStart`）。
   * 亮哪一顆 tab 由它反推，見 `lib/worklogTable.ts` 的 `activeWorklogTab`。
   * 只改參數、不發請求——請求一律由下面那個有閘的 effect 發。
   */
  const storeWeek = weekStart
  const unsent = filters.mode === 'unsent'
  const [unsentLast, unsentThis] = unsentWeeks(thisWeek, lastWeek)
  const activeTab = activeWorklogTab(filters.mode, weekStart, thisWeek, lastWeek)

  function selectTab(tab: WorklogTab) {
    switch (tab) {
      case 'this':
        patchFilters({ mode: 'week' })
        setWeekStart(thisWeek)
        return
      case 'last':
        patchFilters({ mode: 'week' })
        setWeekStart(lastWeek)
        return
      case 'unsent':
        // 未送出的主要那一週（本週）走 store：單日工作區存檔後刷新的是 store 那一份
        patchFilters({ mode: 'unsent' })
        setWeekStart(unsentThis)
        return
      default: {
        const exhaustive: never = tab
        void exhaustive
      }
    }
  }

  /**
   * 換週就載入，**但只在這個工作台真的在看的時候**（見元件註解）。
   *
   * `loadedRef` 記的是「已經為哪一週發過請求」，讓切走再切回來不要重打。
   * **刻意不拿 `week.week_start` 來判斷要不要重打**：那是後端正規化過的值，
   * 萬一與送出的參數不相等，這個 effect 會變成無限重載（同舊週欄）。
   */
  const loadedRef = useRef<string | null>(null)
  useEffect(() => {
    if (!active) return
    if (loadedRef.current === weekStart) return
    loadedRef.current = weekStart
    void loadWeek(weekStart)
  }, [active, weekStart, loadWeek])

  // ── 「未送出」的第二週 ──
  const [extra, setExtra] = useState<ExtraWeek | null>(null)
  const [extraNonce, setExtraNonce] = useState(0)
  const extraSeen = useRef<{ key: string; week: WorklogWeekResponse | null } | null>(null)
  useEffect(() => {
    if (!active || !unsent) return
    const key = `${unsentLast}|${extraNonce}`
    // 同一週、同一次重試、store 那一份也沒刷新過 → 不重打
    if (extraSeen.current && extraSeen.current.key === key && extraSeen.current.week === week) return
    extraSeen.current = { key, week }
    let cancelled = false
    setExtra((current) => ({
      weekStart: unsentLast,
      // 重打時先留著舊的那一份，畫面才不會每次刷新都閃成空白
      week: current?.weekStart === unsentLast ? current.week : null,
      error: null,
      loading: true,
    }))
    api.worklogWeek(unsentLast).then(
      (data) => {
        if (!cancelled) setExtra({ weekStart: unsentLast, week: data, error: null, loading: false })
      },
      (err: unknown) => {
        if (!cancelled)
          setExtra({ weekStart: unsentLast, week: null, error: errorMessage(err), loading: false })
      },
    )
    return () => {
      cancelled = true
    }
  }, [active, unsent, unsentLast, extraNonce, week])

  /**
   * store 那一份能不能拿來畫這個 tab。
   *
   * ⚠️ 切 tab 的瞬間 store 裡還是上一週的資料（新的那一週還在路上）。只看
   * 「有沒有 week」的話，「上週」tab 會先畫出本週的七列，而且日期看起來都對。
   * 所以要比對**回應的** `week_start` 與這個 tab 要的那一週。
   * 請求已經回來、週次卻對不上＝後端的週起點與我們的假設不同：
   * 照「認不出來就拋」寫出來，不拿一週錯位的資料去畫。
   */
  const storeUsable =
    week !== null && week.week_start === storeWeek && (!unsent || storeWeek === unsentThis)
  const storeMismatch =
    week !== null && !storeUsable && !weekLoading && loadedRef.current === storeWeek && !weekError

  const extraUsable =
    extra !== null && extra.weekStart === unsentLast && extra.week !== null && extra.week.week_start === unsentLast
  const extraMismatch =
    extra !== null && extra.week !== null && !extra.loading && extra.week.week_start !== unsentLast

  /** 這個 tab 的全部列（篩選前）。 */
  const items = useMemo<WorklogWeekDay[]>(() => {
    if (!storeUsable || !week) return []
    if (!unsent) return week.days
    // 🔴 兩週缺一週時整個不畫，不拿半份當全部：「未送出」看起來只有本週那兩天，
    // Viewer 會以為上週都交代完了
    if (!extraUsable || !extra?.week) return []
    return [...extra.week.days, ...week.days]
  }, [storeUsable, week, unsent, extraUsable, extra])

  const barMax = useMemo(() => maxDraftHours(items), [items])

  const filter = useMemo(() => worklogTableFilter(filters, weekDayStatus), [filters])
  const sortRows = useCallback((rows: readonly WorklogWeekDay[]) => sortWorklogRows(rows, sort), [sort])

  const paged = usePagedRows({
    items,
    filter,
    sort: sortRows,
    // 篩選、tab、週次或排序一變就回第 1 頁
    resetKey: JSON.stringify({ filters, sort, weekStart }),
    table: 'worklog',
  })

  const columns: DataTableColumn<WorklogWeekDay, WorklogColumnKey>[] = [
    {
      id: 'date',
      header: '日期',
      sortKey: 'date',
      numeric: true,
      headerClassName: 'w-20',
      cell: (day) => formatMonthDay(day.work_date),
    },
    {
      id: 'weekday',
      header: '星期',
      // 查不到就留空（`weekdayLabel` 的註解：寧可只顯示日期，也不編一個星期出來）
      cell: (day) => weekdayLabel(day.weekday),
    },
    {
      id: 'hours',
      header: '草稿時數',
      sortKey: 'hours',
      numeric: true,
      cell: (day) => {
        const hours = draftHoursOf(day)
        const ratio = hoursRatio(hours, barMax)
        return (
          <span className="inline-flex items-center justify-end gap-2">
            {/* 比例條是視覺輔助，數字本身在旁邊；分母是表上最多的那天（見 hoursRatio） */}
            <span aria-hidden className="inline-block h-1.5 w-20 overflow-hidden rounded-full bg-muted">
              <span className="block h-full bg-signal" style={{ width: `${Math.round(ratio * 100)}%` }} />
            </span>
            <span className={cn(hours === null ? 'text-fg-subtle' : 'text-foreground')}>
              {formatHours(day.draft?.hours)}
            </span>
          </span>
        )
      },
    },
    {
      id: 'assignment',
      header: '專案分配',
      cell: (day) => {
        const summary = assignmentOf(day)
        return (
          <span
            className={cn(
              'text-xs',
              summary.kind === 'assigned' ? 'text-foreground' : 'text-fg-dim',
              summary.kind === 'invalid' && 'text-destructive',
            )}
          >
            {assignmentLabel(summary)}
          </span>
        )
      },
    },
    {
      id: 'status',
      header: '狀態',
      cell: (day) => {
        const status = weekDayStatus(day)
        return (
          <span className="flex flex-col items-start gap-0.5">
            <span className={cn('rounded-full border px-2 py-0.5 text-xs whitespace-nowrap', PILL[status])}>
              {WORKLOG_STATUS_LABELS[status]}
            </span>
            {/* ZPlanner 已經有工時的那天要講出來，否則「本地沒有草稿」會被讀成
                「這天什麼都沒有」，然後被重複填一次（沿用舊週欄）。兩本帳不相加 */}
            {day.zplanner ? (
              <span className="text-2xs text-fg-subtle">
                ZPlanner 已填 <span className="metric">{formatHours(day.zplanner.hours)}</span> 小時
              </span>
            ) : null}
          </span>
        )
      },
    },
    {
      id: 'lamp',
      header: '燈號',
      cell: (day) => {
        const lamp = LAMP[weekDayStatus(day)]
        return (
          <span className="inline-flex items-center">
            <span aria-hidden className={cn('inline-block size-2.5 rounded-full', lamp.className)} />
            <span className="sr-only">{lamp.label}</span>
          </span>
        )
      },
    },
  ]

  // 還沒拿到能畫的資料、也沒有任何錯誤＝還在路上。寫「沒有符合條件的天」會讓
  // Viewer 以為這週什麼都沒有
  const loadingNow =
    (!storeUsable && !weekError && !storeMismatch) ||
    (unsent && !extraUsable && !extra?.error && !extraMismatch)
  const emptyHint = loadingNow ? '載入中…' : '沒有符合條件的天'

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="flex flex-col gap-4 px-6 py-4">
        <div className="flex flex-wrap items-center gap-2 border-b border-line">
          <Tabs
            // 翻到本週與上週以外的週時是 null：一顆都不亮，不讓 tab 與週次互相矛盾
            value={activeTab}
            onValueChange={(value) => {
              const tab = parseWorklogTab(value)
              if (tab) selectTab(tab)
            }}
          >
            <TabsList variant="line" aria-label="工時草稿子視圖">
              {WORKLOG_TABS.map((tab) => (
                <TabsTrigger key={tab} value={tab}>
                  {WORKLOG_TAB_LABELS[tab]}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Select
            items={STATUS_ITEMS}
            value={filters.status}
            onValueChange={(next) => {
              // Base UI 的 Select 清除選擇時給 null；那不是「換一種狀態」，不動
              const status = parseWorklogStatusFilter(next)
              if (status) patchFilters({ status })
            }}
          >
            <SelectTrigger size="sm" aria-label="狀態">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUS_ITEMS.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {unsent ? null : (
            <WeekStepper
              week={storeUsable ? week : null}
              atThisWeek={weekStart === thisWeek}
              onShift={shiftWeek}
              onThisWeek={() => setWeekStart(thisWeek)}
            />
          )}

          <RangeLabel unsent={unsent} week={storeUsable ? week : null} extra={extraUsable ? extra?.week ?? null : null} />

          <TableTotal total={paged.total} unit="天" className="ml-auto" />
        </div>

        {weekError ? (
          <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            <p>{weekError}</p>
            <Button size="xs" variant="outline" className="mt-1.5" onClick={() => void loadWeek()}>
              重試
            </Button>
          </div>
        ) : null}
        {storeMismatch && week ? (
          <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            要的是 {storeWeek} 那一週，伺服器回的卻是 {week.week_start} 起的一週；為了不把錯位的日期畫出來，這裡先不顯示。
          </p>
        ) : null}
        {unsent && extra?.error ? (
          <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            <p>上週的資料載入失敗，「未送出」需要本週與上週兩份才畫得完整，所以先不顯示：{extra.error}</p>
            <Button size="xs" variant="outline" className="mt-1.5" onClick={() => setExtraNonce((n) => n + 1)}>
              重試
            </Button>
          </div>
        ) : null}
        {unsent && extraMismatch && extra?.week ? (
          <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            要的是 {unsentLast} 那一週，伺服器回的卻是 {extra.week.week_start} 起的一週；先不顯示。
          </p>
        ) : null}

        <ZplannerNotice week={storeUsable ? week : null} extra={unsent && extraUsable ? extra?.week ?? null : null} />

        <DataTable
          caption="工時草稿清單"
          columns={columns}
          rows={paged.items}
          rowKey={(day) => day.work_date}
          sort={sort}
          onSortChange={(key) => setSort((current) => toggleSort(current, key, WORKLOG_SORT_INITIAL[key]))}
          onRowActivate={(day) => navigate(hashForWorklog(day.work_date))}
          empty={emptyHint}
        />

        <Pager
          page={paged.page}
          pageCount={paged.pageCount}
          pageSize={paged.pageSize}
          onPageChange={paged.setPage}
          onPageSizeChange={paged.setPageSize}
          label="工時草稿清單分頁"
        />

        <p className="text-2xs text-muted-foreground">
          專案分配在產生草稿時一律留空（ADR-0008），由你在當天的草稿裡逐段選擇；這裡只顯示你已經選了幾段。
        </p>
      </div>
    </div>
  )
}

/**
 * 篩選列上的範圍說明。**週次一律讀回應的 `week_start`／`week_end`**，不讀自己
 * 送出的參數（後端會正規化日期，拿參數當答案遲早顯示一個與內容不符的範圍）。
 * 載入完成前不顯示任何日期。
 */
function RangeLabel({
  unsent,
  week,
  extra,
}: {
  unsent: boolean
  week: WorklogWeekResponse | null
  extra: WorklogWeekResponse | null
}) {
  if (!week) return null
  if (unsent) {
    if (!extra) return null
    return (
      // 🔴 範圍一定要寫出來：API 一次只給一週，更早的未送出草稿不在這張表裡
      <span className="text-xs text-fg-dim">
        範圍：上週與本週（<span className="metric">{formatMonthDay(extra.week_start)}–{formatMonthDay(week.week_end)}</span>
        ）；更早的未送出草稿不在這裡
      </span>
    )
  }
  const { totals, zplanner } = week
  return (
    <span className="text-xs text-fg-dim">
      草稿 <span className="metric">{formatHours(totals.draft_hours)}</span> 小時
      {' · '}已送出 <span className="metric">{totals.days_submitted}</span> / <span className="metric">{week.days.length}</span> 天
      {zplanner.available ? (
        <>
          {' · '}已填 ZPlanner <span className="metric">{formatHours(zplanner.total_hours)}</span> 小時
          {/* 目標只在 ZPlanner 真的給了時才寫；取不到時欄位整個不存在，不補 40 */}
          {zplanner.target_hours ? (
            <>
              {' '}／目標 <span className="metric">{formatHours(zplanner.target_hours)}</span>（還差{' '}
              <span className="metric">{remainingHours(zplanner.target_hours, zplanner.total_hours)}</span>）
            </>
          ) : null}
        </>
      ) : null}
    </span>
  )
}

/**
 * ZPlanner 不可用的說明。🔴 **不可以靜默隱藏**：那時每一列的「ZPlanner 已填」
 * 都不見了，沒有這句話 Viewer 只會以為功能壞了、或以為那幾天真的沒填。
 * `reason` 是後端給的繁中句子，直接顯示。
 */
function ZplannerNotice({
  week,
  extra,
}: {
  week: WorklogWeekResponse | null
  extra: WorklogWeekResponse | null
}) {
  const reasons = [week, extra]
    .map((w) => (w && !w.zplanner.available ? w.zplanner.reason : null))
    .filter((reason): reason is string => reason !== null)
  if (reasons.length === 0) return null
  return (
    <p className="rounded-lg border border-caution-line px-3 py-2 text-xs text-caution">
      {[...new Set(reasons)].join('　')}　本地草稿不受影響，照常顯示。
    </p>
  )
}

/**
 * 翻週（與已移除的舊週欄同一套行為）：◂ 上一週、▸ 下一週、不在本週時
 * 出現「回到本週」。Viewer 需要翻回更早的週補填工時，tabs 只是其中兩週的捷徑。
 *
 * 標題**讀回應的** `week_start`／`week_end`；載入完成（且週次對得上）前不顯示任何
 * 日期——顯示一個「我們猜的」範圍，比空著更難發現它與內容不符。
 */
function WeekStepper({
  week,
  atThisWeek,
  onShift,
  onThisWeek,
}: {
  week: WorklogWeekResponse | null
  atThisWeek: boolean
  onShift: (delta: number) => void
  onThisWeek: () => void
}) {
  return (
    <div className="flex items-center gap-1">
      <Button size="icon-xs" variant="ghost" aria-label="上一週" onClick={() => onShift(-1)}>
        ◂
      </Button>
      <h2 aria-label="週次" className="metric min-w-24 text-center text-xs font-semibold">
        {week ? `${formatMonthDay(week.week_start)}–${formatMonthDay(week.week_end)}` : '　'}
      </h2>
      <Button size="icon-xs" variant="ghost" aria-label="下一週" onClick={() => onShift(1)}>
        ▸
      </Button>
      {atThisWeek ? null : (
        <Button size="xs" variant="ghost" onClick={onThisWeek}>
          回到本週
        </Button>
      )}
    </div>
  )
}
