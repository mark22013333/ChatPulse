import { useMemo, useState } from 'react'
import { HashIcon, Loader2Icon, PinIcon, PinOffIcon, SearchIcon, UserIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { relativeTime, spaceTypeLabel } from '@/lib/format'
import { cn } from '@/lib/utils'
import { filterSpaces, sortByPinned, useSpacesStore } from '@/store/spaces'
import type { Space } from '@/lib/types'

function SpaceRow({
  space,
  busy,
  onTogglePin,
}: {
  space: Space
  busy: boolean
  onTogglePin: (space: Space) => void
}) {
  return (
    <li className="border-line flex items-center gap-3 border-b py-2 last:border-0">
      {space.type === 'SPACE' ? (
        <HashIcon className="text-fg-subtle size-4 shrink-0" aria-hidden />
      ) : (
        <UserIcon className="text-fg-subtle size-4 shrink-0" aria-hidden />
      )}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm">{space.displayName || space.id}</span>
        <span className="text-fg-dim text-2xs block">
          {spaceTypeLabel(space.type)}　最後活動 {relativeTime(space.lastActiveTime)}
        </span>
      </span>
      <Button
        size="sm"
        variant={space.pinned ? 'default' : 'outline'}
        disabled={busy}
        onClick={() => onTogglePin(space)}
        aria-pressed={space.pinned ?? false}
      >
        {space.pinned ? <PinOffIcon /> : <PinIcon />}
        {space.pinned ? '取消釘選' : '釘選'}
      </Button>
    </li>
  )
}

/**
 * Space 偏好頁：釘選管理。
 *
 * 後端從一開始就支援釘選（`preferences.pinned_space_ids` 有 DB 欄位、
 * `GET /spaces` 每一筆都帶 `pinned`），但前端完全沒有介面。436 個 Space
 * 的規模下，這是最直接的一個補洞。
 */
export function SpacePrefsPage() {
  const items = useSpacesStore((s) => s.items)
  const loading = useSpacesStore((s) => s.loading)
  const pinning = useSpacesStore((s) => s.pinning)
  const togglePin = useSpacesStore((s) => s.togglePin)
  const [search, setSearch] = useState('')

  const pinned = useMemo(() => items.filter((s) => s.pinned), [items])
  const candidates = useMemo(
    () => sortByPinned(filterSpaces(items, search)).slice(0, 40),
    [items, search],
  )

  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <div>
          <h2 className="text-md font-semibold">釘選的 Space</h2>
          <p className="text-fg-dim mt-1 text-xs leading-relaxed">
            釘選的 Space 會排在清單最前面。你加入了 {items.length} 個 Space，
            常用的其實只有幾個。
          </p>
        </div>

        {loading && items.length === 0 ? (
          <p className="text-fg-dim flex items-center gap-2 text-xs">
            <Loader2Icon className="size-3.5 animate-spin" />
            正在載入 Space…
          </p>
        ) : pinned.length === 0 ? (
          <p className="text-fg-dim text-sm">還沒有釘選任何 Space。在下面搜尋並按「釘選」。</p>
        ) : (
          <ul>
            {pinned.map((space) => (
              <SpaceRow key={space.id} space={space} busy={pinning} onTogglePin={togglePin} />
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-md font-semibold">全部 Space</h2>
        <div className="relative">
          <SearchIcon className="text-fg-subtle pointer-events-none absolute top-2.5 left-2.5 size-4" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={`搜尋 ${items.length} 個 Space 名稱…`}
            className="pl-8"
            aria-label="搜尋 Space"
          />
        </div>
        <ul className={cn(candidates.length === 0 && 'hidden')}>
          {candidates.map((space) => (
            <SpaceRow key={space.id} space={space} busy={pinning} onTogglePin={togglePin} />
          ))}
        </ul>
        {candidates.length === 0 ? (
          <p className="text-fg-dim text-sm">查無符合的 Space。</p>
        ) : (
          <p className="text-fg-dim text-2xs">
            最多顯示 40 筆，用搜尋縮小範圍。
          </p>
        )}
      </section>
    </div>
  )
}
