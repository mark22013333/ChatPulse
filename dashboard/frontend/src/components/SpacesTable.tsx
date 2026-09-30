import { useCallback, useMemo, useState } from 'react'
import { Loader2Icon, PencilIcon, RefreshCwIcon, SearchIcon, StarIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { DataTable, type DataTableColumn } from '@/components/table/DataTable'
import { Pager, TableTotal } from '@/components/table/Pager'
import { usePagedRows } from '@/components/table/usePagedRows'
import { useSpaceRename } from '@/components/SpaceRenameDialog'
import { relativeTime, spaceTypeLabel } from '@/lib/format'
import { mentionTruncation, sortSpaces } from '@/lib/listSort'
import { hashForSummary } from '@/lib/route'
import {
  DEFAULT_SPACE_TABLE_FILTERS,
  SPACE_TABS,
  SPACE_TAB_LABELS,
  SPACE_TYPE_FILTERS,
  SPACE_TYPE_FILTER_LABELS,
  headerSortOf,
  lastActiveLabel,
  nextSpaceSort,
  nextSummarySort,
  outstandingBySpace,
  parseSpaceTab,
  parseSpaceTypeFilter,
  SUMMARY_STATUS_LABELS,
  sortBySummaryStatus,
  spaceTableFilter,
  summaryStatusOf,
  type SpaceColumnKey,
  type SpaceTableFilters,
} from '@/lib/spaceTable'
import { cn } from '@/lib/utils'
import { useRouter } from '@/router/useRouter'
import { useViewerListSort } from '@/store/listSort'
import { isOutstanding, useMentionsStore } from '@/store/mentions'
import { useSpacesStore } from '@/store/spaces'
import type { SortDirection } from '@/lib/paginate'
import type { Space } from '@/lib/types'

const TYPE_ITEMS = SPACE_TYPE_FILTERS.map((value) => ({
  value,
  label: SPACE_TYPE_FILTER_LABELS[value],
}))

/**
 * 摘要工作台的 Space 表格（設計規格 2026-10-01 §4）：tabs → 釘選卡片列 →
 * 篩選列 → 表格 → 分頁器。點一列整頁切到 `#/summary/:key` 的 Summary 工作區。
 *
 * 🔴 **這個元件收起來時不會卸載**（AppShell 用 inert ＋ 透明把它移出版面流）。
 * tabs、篩選、頁碼都是這裡的元件 state，返回清單時它們還在，靠的就是這件事；
 * 哪天有人把 AppShell 改成條件渲染，這些狀態會全部歸零——`AppShell.test.tsx`
 * 守著。
 *
 * 🔴 篩選與排序一律經 `usePagedRows`／`lib/paginate.ts` 對**全部** Space 做完
 * 再切頁。這裡不自己 `slice`、不自己排當頁。
 *
 * 欄位只放拿得到資料的（規格 §4.3 的「今日訊息」已決定不做）。「Summary」欄
 * 用後端給的 `lastSummaryAt` 對 `lastActiveTime` 判定，不多打 Google API；
 * 判定規則與「認不出來顯示 —」的理由在 `lib/spaceTable.ts` 的 `summaryStatusOf`。
 */
export function SpacesTable() {
  const items = useSpacesStore((state) => state.items)
  const total = useSpacesStore((state) => state.total)
  const cached = useSpacesStore((state) => state.cached)
  const cachedAt = useSpacesStore((state) => state.cachedAt)
  const loading = useSpacesStore((state) => state.loading)
  const refreshing = useSpacesStore((state) => state.refreshing)
  const error = useSpacesStore((state) => state.error)
  const pinning = useSpacesStore((state) => state.pinning)
  const load = useSpacesStore((state) => state.load)
  const togglePin = useSpacesStore((state) => state.togglePin)

  const mentionItems = useMentionsStore((state) => state.items)
  const mentionCounts = useMentionsStore((state) => state.counts)
  const mentionsLoading = useMentionsStore((state) => state.loading)
  const mentionsError = useMentionsStore((state) => state.error)

  // 點列走 navigate 而不是直接 select：「點擊」與「貼網址」走同一條路徑（§6.6）
  const { navigate } = useRouter()
  const open = (space: Space) => navigate(hashForSummary(space.id))

  const { openRename, dialog: renameDialog } = useSpaceRename()

  // 排序偏好沿用舊左欄那一份（store/listSort）：選過的排序換成表格之後仍然有效
  const { prefs, setSpaceSort } = useViewerListSort()
  const spaceSort = prefs.space
  // Summary 欄的排序不在偏好的排序鍵裡（理由見 sortBySummaryStatus）。
  // 有值＝它正在生效；點任何其他欄就清掉，回到偏好那一份——同一時間只有一份在生效
  const [summarySort, setSummarySort] = useState<SortDirection | null>(null)

  const [filters, setFilters] = useState<SpaceTableFilters>(DEFAULT_SPACE_TABLE_FILTERS)
  const patchFilters = (patch: Partial<SpaceTableFilters>) =>
    setFilters((current) => ({ ...current, ...patch }))

  const outstanding = useMemo(
    () => outstandingBySpace(mentionItems, isOutstanding),
    [mentionItems],
  )

  /**
   * Mention 數現在能不能拿來下結論。
   *
   * ⚠️ 「清單是空的」有三種意思：真的沒有、還在載入、載入失敗。只有第一種
   * 可以顯示 0——另外兩種顯示 0 就等於告訴 Viewer「這個 Space 沒有 Mention」，
   * 而那是我們不知道的事。`counts` 來自 `/me` 或清單本身，總數 > 0 但清單
   * 是空的＝還沒載入。
   */
  const mentionsKnown =
    !mentionsError &&
    !(mentionsLoading && mentionItems.length === 0) &&
    !(mentionItems.length === 0 && mentionCounts.pending + mentionCounts.resolved > 0)

  /**
   * 收件匣只載入最新 200 筆（`lib/listSort.ts` 的 `mentionTruncation` 記了原因）。
   * 只有「有待處理的沒載入」才影響這張表——少掉的全是已處理的話，每個
   * Space 的待處理數仍然是完整的。
   */
  const loadedOutstanding = useMemo(
    () => [...outstanding.values()].reduce((sum, n) => sum + n, 0),
    [outstanding],
  )
  const missingOutstanding =
    mentionsKnown && mentionTruncation(mentionCounts, mentionItems.length)
      ? Math.max(0, mentionCounts.pending - loadedOutstanding)
      : 0

  const filter = useMemo(() => spaceTableFilter(filters, outstanding), [filters, outstanding])
  const sort = useCallback(
    (list: readonly Space[]) =>
      summarySort
        ? sortBySummaryStatus(list, summarySort, spaceSort.pinnedFirst)
        : sortSpaces(list, spaceSort),
    [spaceSort, summarySort],
  )

  const paged = usePagedRows({
    items,
    filter,
    sort,
    // 篩選或排序一變就回第 1 頁：停在第 4 頁看新的排序，看到的是「第 76 名之後」
    resetKey: JSON.stringify({ filters, spaceSort, summarySort }),
    table: 'summary',
  })

  // 釘選卡片列不受 tabs／篩選影響：它是「常用的入口」，不是篩選結果的一部分。
  // 順序沿用目前的排序（不套釘選置頂——這裡全都是釘選的）
  const pinned = useMemo(
    () => sortSpaces(items.filter((space) => space.pinned), { ...spaceSort, pinnedFirst: false }),
    [items, spaceSort],
  )

  const mentionCell = (space: Space) =>
    mentionsKnown ? (
      <span className={cn((outstanding.get(space.id) ?? 0) > 0 ? 'font-semibold text-foreground' : 'text-fg-dim')}>
        {outstanding.get(space.id) ?? 0}
      </span>
    ) : (
      <span className="text-fg-dim">
        —<span className="sr-only">（Mention 尚未載入）</span>
      </span>
    )

  const columns: DataTableColumn<Space, SpaceColumnKey>[] = [
    {
      id: 'pin',
      header: <span className="sr-only">釘選</span>,
      headerClassName: 'w-10',
      cell: (space) => (
        <button
          type="button"
          // 名稱固定、狀態交給 aria-pressed：切換時改名稱的話讀屏會唸成兩顆不同的按鈕
          aria-label={`釘選「${space.displayName || space.id}」`}
          aria-pressed={space.pinned === true}
          disabled={pinning}
          onClick={() => void togglePin(space)}
          className="-m-1 rounded p-1 text-fg-dim hover:text-foreground disabled:opacity-50"
        >
          <StarIcon
            aria-hidden
            className={cn('size-4', space.pinned && 'fill-current text-caution')}
          />
        </button>
      ),
    },
    {
      id: 'name',
      header: 'Space 名稱',
      sortKey: 'name',
      cell: (space) => (
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="truncate font-medium">{space.displayName || space.id}</span>
          {/* 名字是猜的就說出來：它決定「這一列是不是我要找的對話」，是決策
              資訊，不該藏在 tooltip 裡（規格 §10.3）。沿用舊左欄的文案 */}
          {space.renamable && space.nameSource !== 'dm_manual' ? (
            <span className="shrink-0 text-xs text-caution">名稱為推測</span>
          ) : null}
          {space.renamable ? (
            <button
              type="button"
              onClick={() => openRename(space)}
              aria-label={
                space.nameSource === 'dm_manual'
                  ? `改「${space.displayName}」這個名字`
                  : `為 ${space.displayName} 取名（目前的名字是推測的）`
              }
              className="shrink-0 rounded p-1 text-fg-dim hover:bg-accent hover:text-foreground"
            >
              <PencilIcon aria-hidden className="size-3" />
            </button>
          ) : null}
        </span>
      ),
    },
    {
      id: 'type',
      header: '類型',
      sortKey: 'type',
      cell: (space) => (
        <span className="inline-block rounded-full bg-muted px-2 py-0.5 text-xs whitespace-nowrap text-muted-foreground">
          {spaceTypeLabel(space.type)}
        </span>
      ),
    },
    {
      id: 'activity',
      header: '最後活動',
      sortKey: 'activity',
      numeric: true,
      cell: (space) => lastActiveLabel(space.lastActiveTime),
    },
    {
      id: 'mentions',
      // 算的是待處理（pending ＋ manual），與收件匣「待處理」分頁同一個判準
      header: '待處理 Mention',
      numeric: true,
      cell: mentionCell,
    },
    {
      id: 'summary',
      header: 'Summary',
      sortKey: 'summary',
      cell: (space) => {
        const status = summaryStatusOf(space)
        return (
          <span
            className={cn(
              'text-xs whitespace-nowrap',
              status === 'stale' ? 'font-semibold text-caution' : 'text-fg-dim',
            )}
          >
            {SUMMARY_STATUS_LABELS[status]}
            {status === 'unknown' ? <span className="sr-only">（無法判斷 Summary 是否最新）</span> : null}
          </span>
        )
      },
    },
  ]

  const emptyHint =
    loading && items.length === 0
      ? '正在載入 Space…'
      : filters.tab === 'mentions' && !mentionsKnown
        ? // 不知道就說不知道：顯示「沒有」會讓 Viewer 以為沒有人在找他
          '還沒拿到 Mention 資料，無法判斷哪些 Space 有新 Mention'
        : '沒有符合條件的 Space'

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="flex flex-col gap-4 px-6 py-4">
        <div className="flex flex-wrap items-center gap-2 border-b border-line">
          <Tabs
            value={filters.tab}
            onValueChange={(value) => {
              const tab = parseSpaceTab(value)
              if (tab) patchFilters({ tab })
            }}
          >
            <TabsList variant="line" aria-label="Space 子視圖">
              {SPACE_TABS.map((tab) => (
                <TabsTrigger key={tab} value={tab}>
                  {SPACE_TAB_LABELS[tab]}
                  {tab === 'pinned' ? (
                    <span className="metric ml-1 text-2xs text-fg-dim">{pinned.length}</span>
                  ) : null}
                  {tab === 'mentions' && mentionsKnown ? (
                    <span className="metric ml-1 text-2xs text-fg-dim">{outstanding.size}</span>
                  ) : null}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <Button
            size="xs"
            variant="ghost"
            className="ml-auto"
            onClick={() => void load({ refresh: true })}
            disabled={refreshing || loading}
          >
            {refreshing ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />}
            強制刷新
          </Button>
        </div>

        <section aria-label="已釘選的 Space" className="flex flex-col gap-2">
          <p className="text-xs text-fg-dim">★ 已釘選的 Space（點表格中的 ☆ 加入）</p>
          {pinned.length > 0 ? (
            <ul className="grid grid-cols-[repeat(auto-fill,minmax(13rem,1fr))] gap-2.5">
              {pinned.map((space) => (
                <li key={space.id} className="contents">
                  <button
                    type="button"
                    onClick={() => open(space)}
                    className="rounded-lg border border-line bg-surface px-3 py-2.5 text-left shadow-xs hover:bg-signal-wash"
                  >
                    <span className="block truncate text-sm font-medium text-signal">
                      {space.displayName || space.id}
                    </span>
                    <span className="block truncate text-xs text-fg-dim">
                      {spaceTypeLabel(space.type)} · 最後活動 {relativeTime(space.lastActiveTime)}
                      {mentionsKnown && (outstanding.get(space.id) ?? 0) > 0
                        ? ` · ${outstanding.get(space.id)} 則待處理 Mention`
                        : ''}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-fg-dim">還沒有釘選任何 Space。</p>
          )}
        </section>

        <div className="flex flex-wrap items-center gap-2">
          <div className="relative w-64 max-w-full">
            <SearchIcon
              aria-hidden
              className="pointer-events-none absolute top-2.5 left-2.5 size-3.5 text-muted-foreground"
            />
            <Input
              value={filters.search}
              onChange={(event) => patchFilters({ search: event.target.value })}
              placeholder="Space 名稱"
              aria-label="搜尋 Space 名稱"
              className="pl-7"
            />
          </div>

          <Select
            items={TYPE_ITEMS}
            value={filters.type}
            onValueChange={(next) => {
              // Base UI 的 Select 清除選擇時給 null；那不是「換一種類型」，不動
              const type = parseSpaceTypeFilter(next)
              if (type) patchFilters({ type })
            }}
          >
            <SelectTrigger size="sm" aria-label="類型">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TYPE_ITEMS.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
            <Checkbox
              checked={spaceSort.pinnedFirst}
              onCheckedChange={(checked) => setSpaceSort({ pinnedFirst: checked === true })}
            />
            釘選置頂
          </label>

          <TableTotal total={paged.total} unit="個 Space" className="ml-auto" />
        </div>

        {error ? (
          <p className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </p>
        ) : null}

        {/* 靜默少給資料一律明示（CLAUDE.md 硬規則）：數字看起來是完整的，
            實際少了較舊的待處理 Mention */}
        {missingOutstanding > 0 ? (
          <p className="rounded-lg border border-caution-line px-3 py-2 text-xs text-caution">
            另有 {missingOutstanding} 則較舊的待處理 Mention 沒有載入（收件匣只載入最新{' '}
            {mentionItems.length} 則），「待處理 Mention」欄與「有新 Mention」分頁不含它們。
          </p>
        ) : null}
        {mentionsError ? (
          <p className="rounded-lg border border-caution-line px-3 py-2 text-xs text-caution">
            Mention 載入失敗，「待處理 Mention」欄暫時無法顯示：{mentionsError}
          </p>
        ) : null}

        <DataTable
          caption="Space 清單"
          columns={columns}
          rows={paged.items}
          rowKey={(space) => space.id}
          sort={headerSortOf(spaceSort.sort, summarySort)}
          onSortChange={(key) => {
            if (key === 'summary') {
              setSummarySort(nextSummarySort(summarySort))
              return
            }
            // 從 Summary 欄點回來＝「從別欄點過來」，要得到該欄第一次點的方向。
            // 偏好裡若剛好是 activity-desc，直接拿它當基準會被翻成 asc（看起來像點了兩次）。
            // ⚠️ 基準必須是「三欄的第一次方向都不會被它翻轉」的鍵：名稱／類型有了降冪之後，
            // 拿 'name' 當基準會讓點名稱直接變成 name-desc。activity-asc 對三欄都成立：
            // 最後活動翻成 desc、名稱與類型得到升冪
            const base = summarySort ? 'activity-asc' : spaceSort.sort
            setSummarySort(null)
            setSpaceSort({ sort: nextSpaceSort(base, key) })
          }}
          onRowActivate={open}
          empty={emptyHint}
        />

        <Pager
          page={paged.page}
          pageCount={paged.pageCount}
          pageSize={paged.pageSize}
          onPageChange={paged.setPage}
          onPageSizeChange={paged.setPageSize}
          label="Space 清單分頁"
        />

        <p className="text-2xs text-muted-foreground">
          共 {total || items.length} 個 Space · {cached ? `快取於 ${relativeTime(cachedAt)}` : '即時資料'}
          {/* 「強制刷新」做什麼，原本只活在那顆按鈕的 tooltip 裡（規格 §10.3） */}
          {cached ? '。強制刷新會跳過快取，向 Google 重新取回' : ''}
        </p>
      </div>

      {renameDialog}
    </div>
  )
}
