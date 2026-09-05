import { useEffect, useMemo } from 'react'
import { LayersIcon, Loader2Icon, RefreshCwIcon, SearchIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { SpaceList } from '@/components/SpaceList'
import { relativeTime } from '@/lib/format'
import { filterSpaces, useSpacesStore } from '@/store/spaces'

/** 左側 Space 導覽（搜尋 + 強制刷新 + 虛擬滾動清單）。 */
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
  const select = useSpacesStore((state) => state.select)
  const load = useSpacesStore((state) => state.load)

  useEffect(() => {
    if (items.length === 0) void load()
  }, [items.length, load])

  const visible = useMemo(() => filterSpaces(items, search), [items, search])

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
            title="跳過 5 分鐘快取，向 Google 重新取回"
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
      </div>

      {error ? (
        <p className="shrink-0 border-b border-destructive/30 bg-destructive/10 px-3 py-2 text-[11px] text-destructive">
          {error}
        </p>
      ) : null}

      <SpaceList
        spaces={visible}
        loading={loading && items.length === 0}
        selectedId={selectedId}
        onSelect={(space) => select(space.id)}
      />

      <div className="flex shrink-0 items-center justify-between border-t border-border px-3 py-1.5 text-[10px] text-muted-foreground">
        <span>
          顯示 {visible.length} / {total || items.length}
        </span>
        <span>{cached ? `快取於 ${relativeTime(cachedAt)}` : '即時資料'}</span>
      </div>
    </div>
  )
}
