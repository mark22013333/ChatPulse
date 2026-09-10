import { useMemo, useState } from 'react'
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
import { onEnter } from '@/lib/keyboard'
import { hashForSummary } from '@/lib/route'
import { useRouter } from '@/router/useRouter'
import { filterSpaces, sortByPinned, useSpacesStore } from '@/store/spaces'
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
  const load = useSpacesStore((state) => state.load)
  const rename = useSpacesStore((state) => state.rename)

  // 點清單走 navigate 而不是直接 select：這樣「點擊」與「貼網址」走同一條
  // 路徑，只有一種行為要維護（設計規格 §6.6）
  const { navigate } = useRouter()

  // 正在改名的空間；null＝對話框關著
  const [renaming, setRenaming] = useState<Space | null>(null)
  const [aliasDraft, setAliasDraft] = useState('')

  // 首次載入搬到 AppShell 的 bootstrap（設計規格 §7.4）：釘選、命令面板、
  // Reference Space 三處都要 spaces，不該由「哪個畫面剛好先掛載」決定何時載入。

  // 釘選的排前面。過濾與排序刻意分開（filterSpaces 有 14 項既有測試打在上面）
  const visible = useMemo(() => sortByPinned(filterSpaces(items, search)), [items, search])

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
              // onEnter 會擋掉輸入法組字中的 Enter——注音選字時按 Enter
              // 是「選這個字」，不是「儲存」。見 lib/keyboard.ts
              onKeyDown={onEnter(() => void submitRename())}
            />
            <p className="text-xs text-muted-foreground">
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
