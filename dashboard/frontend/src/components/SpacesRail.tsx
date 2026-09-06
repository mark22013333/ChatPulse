import { useEffect, useMemo, useState } from 'react'
import { LayersIcon, Loader2Icon, RefreshCwIcon, SearchIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { SpaceList } from '@/components/SpaceList'
import { relativeTime } from '@/lib/format'
import { filterSpaces, useSpacesStore } from '@/store/spaces'
import type { Space } from '@/lib/types'

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
  const rename = useSpacesStore((state) => state.rename)

  // 正在改名的空間；null＝對話框關著
  const [renaming, setRenaming] = useState<Space | null>(null)
  const [aliasDraft, setAliasDraft] = useState('')

  useEffect(() => {
    if (items.length === 0) void load()
  }, [items.length, load])

  const visible = useMemo(() => filterSpaces(items, search), [items, search])

  const openRename = (space: Space) => {
    // 自動猜的名字不預填——那是猜的，讓使用者從空白開始比較清楚；
    // 自己取過的才預填，方便微調。
    setAliasDraft(space.nameSource === 'dm_manual' ? space.displayName : '')
    setRenaming(space)
  }

  const submitRename = async () => {
    if (!renaming) return
    await rename(renaming.id, aliasDraft)
    setRenaming(null)
  }

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
        onRename={openRename}
      />

      <Dialog open={renaming !== null} onOpenChange={(open) => !open && setRenaming(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>為這個空間取個名字</DialogTitle>
            <DialogDescription>
              私訊在 Google Chat 沒有名稱，我們只能從訊息裡認出對方是誰——
              對方沒在任何群組被 @ 過就認不出來。你知道他是誰，直接取一個好認的名字。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-2">
            <Label htmlFor="space-alias">名字</Label>
            <Input
              id="space-alias"
              value={aliasDraft}
              autoFocus
              maxLength={60}
              placeholder="例如：王小明（某某廠商）"
              onChange={(e) => setAliasDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void submitRename()
              }}
            />
            <p className="text-[11px] text-muted-foreground">
              清空後儲存＝取消自訂，回到自動辨識的結果。這個名字只有你看得到，
              不會改動 Google Chat。
            </p>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setRenaming(null)}>
              取消
            </Button>
            <Button onClick={() => void submitRename()}>儲存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <div className="flex shrink-0 items-center justify-between border-t border-border px-3 py-1.5 text-[10px] text-muted-foreground">
        <span>
          顯示 {visible.length} / {total || items.length}
        </span>
        <span>{cached ? `快取於 ${relativeTime(cachedAt)}` : '即時資料'}</span>
      </div>
    </div>
  )
}
