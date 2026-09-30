import { useMemo } from 'react'
import { LayersIcon, Loader2Icon, RefreshCwIcon, SearchIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { SpaceList } from '@/components/SpaceList'
import { useSpaceRename } from '@/components/SpaceRenameDialog'
import { relativeTime } from '@/lib/format'
import { SPACE_SORT_KEYS, SPACE_SORT_LABELS, sortSpaces, type SpaceSortKey } from '@/lib/listSort'
import { hashForSummary } from '@/lib/route'
import { useRouter } from '@/router/useRouter'
import { useViewerListSort } from '@/store/listSort'
import { filterSpaces, useSpacesStore } from '@/store/spaces'

/**
 * 左側 Space 導覽（搜尋 + 強制刷新 + 虛擬滾動清單）。
 *
 * ⚠️ 2026-10-01 起摘要工作台改用 `SpacesTable`（整頁表格），這個元件已經
 * 沒有任何地方掛載。檔案保留、是否移除由維護者決定。
 */
export function SpacesRail() {
  const items = useSpacesStore((state) => state.items)
  const total = useSpacesStore((state) => state.total)
  const cached = useSpacesStore((state) => state.cached)
  const cachedAt = useSpacesStore((state) => state.cachedAt)
  const loading = useSpacesStore((state) => state.loading)
  const refreshing = useSpacesStore((state) => state.refreshing)
  const error = useSpacesStore((state) => state.error)
  const search = useSpacesStore((state) => state.search)
  const selectedId = useSpacesStore((state) => state.selectedId)
  const setSearch = useSpacesStore((state) => state.setSearch)
  const load = useSpacesStore((state) => state.load)

  // 點清單走 navigate 而不是直接 select：這樣「點擊」與「貼網址」走同一條
  // 路徑，只有一種行為要維護（設計規格 §6.6）
  const { navigate } = useRouter()

  // 改名對話框與 SpacesTable 共用（見 SpaceRenameDialog.tsx）
  const { openRename, dialog: renameDialog } = useSpaceRename()

  // 首次載入搬到 AppShell 的 bootstrap（設計規格 §7.4）：釘選、命令面板、
  // Reference Space 三處都要 spaces，不該由「哪個畫面剛好先掛載」決定何時載入。

  const { prefs, setSpaceSort } = useViewerListSort()
  const spaceSort = prefs.space

  // 過濾與排序刻意分開（filterSpaces 有 14 項既有測試打在上面）。
  // 以前這裡是 sortByPinned：保留後端順序、釘選插隊到最前面——但後端順序
  // 本來就不保證依時間，於是 Viewer 看到「釘選 22 小時前」排在「5 分鐘前」
  // 之前（2026-09-30 回報）。現在一律前端依所選欄位排，釘選置頂變成選項。
  const visible = useMemo(
    () => sortSpaces(filterSpaces(items, search), spaceSort),
    [items, search, spaceSort],
  )

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 space-y-2 border-b border-border px-3 py-2.5">
        <div className="flex items-center gap-2">
          <LayersIcon className="size-3.5 text-muted-foreground" />
          <h2 className="text-xs font-semibold tracking-wide">Space 清單</h2>
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

        <div className="relative">
          <SearchIcon className="pointer-events-none absolute top-2 left-2.5 size-3.5 text-muted-foreground" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={`搜尋 ${total || items.length} 個 Space 名稱…`}
            className="h-7 pl-7"
          />
        </div>

        <div className="flex items-center gap-2">
          <Select
            items={SPACE_SORT_LABELS}
            value={spaceSort.sort}
            onValueChange={(next) => {
              // Base UI 的 Select 清除選擇時會給 null；那不是「換一種排序」，不動
              if (!next) return
              setSpaceSort({ sort: next as SpaceSortKey })
            }}
          >
            <SelectTrigger size="sm" className="h-7 min-w-0 flex-1 text-xs" aria-label="Space 排序方式">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SPACE_SORT_KEYS.map((key) => (
                <SelectItem key={key} value={key}>
                  {SPACE_SORT_LABELS[key]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <label className="flex shrink-0 cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
            <Checkbox
              checked={spaceSort.pinnedFirst}
              onCheckedChange={(checked) => setSpaceSort({ pinnedFirst: checked === true })}
            />
            釘選置頂
          </label>
        </div>
      </div>

      {error ? (
        <p className="shrink-0 border-b border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </p>
      ) : null}

      <SpaceList
        spaces={visible}
        loading={loading && items.length === 0}
        label="要做摘要的 Space"
        selectedId={selectedId}
        onSelect={(space) => navigate(hashForSummary(space.id))}
        onRename={openRename}
      />

      {renameDialog}

      <div className="shrink-0 border-t border-border px-3 py-1.5 text-2xs text-muted-foreground">
        <div className="flex items-center justify-between gap-2">
          <span>
            顯示 {visible.length} / {total || items.length}
          </span>
          <span>{cached ? `快取於 ${relativeTime(cachedAt)}` : '即時資料'}</span>
        </div>
        {/* 「強制刷新」做什麼，原本只活在那顆按鈕的 tooltip 裡（規格 §10.3）。
            放在這裡是因為它就是在解釋上面那個「快取於」。**自己一行**：
            併進上面那個 justify-between 的兩欄會在 280px 的側欄撐爆。 */}
        {cached ? <p className="pt-0.5">強制刷新會跳過快取，向 Google 重新取回</p> : null}
      </div>
    </div>
  )
}
