import { useRef } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { HashIcon, Loader2Icon, PencilIcon, PinIcon, UserIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { relativeTime, spaceTypeLabel } from '@/lib/format'
import { Checkbox } from '@/components/ui/checkbox'
import type { Space } from '@/lib/types'

interface SpaceListProps {
  spaces: Space[]
  loading?: boolean
  emptyHint?: string
  /** 單選模式（摘要工作台） */
  selectedId?: string | null
  onSelect?: (space: Space) => void
  /** 複選模式（Draft Reply 的 Reference Space） */
  checkedIds?: string[]
  onToggle?: (space: Space) => void
  /** 給 renamable 的空間（私訊／未命名）取別名。有給才會出現鉛筆。 */
  onRename?: (space: Space) => void
  className?: string
}

/**
 * Space 清單。436 筆一次載入，因此用虛擬滾動只掛載可視範圍的列，
 * 搜尋後仍是同一個虛擬清單（規格 5.1、契約前端注意事項 5）。
 */
export function SpaceList({
  spaces,
  loading = false,
  emptyHint = '查無符合的 Space',
  selectedId,
  onSelect,
  checkedIds,
  onToggle,
  onRename,
  className,
}: SpaceListProps) {
  const parentRef = useRef<HTMLDivElement>(null)
  const multi = Boolean(onToggle)
  const checked = new Set(checkedIds ?? [])

  const virtualizer = useVirtualizer({
    count: spaces.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 52,
    overscan: 12,
  })

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center gap-2 p-6 text-xs text-muted-foreground">
        <Loader2Icon className="size-3.5 animate-spin" />
        正在載入 Space…
      </div>
    )
  }

  if (spaces.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-center text-xs text-muted-foreground">
        {emptyHint}
      </div>
    )
  }

  return (
    <div ref={parentRef} className={cn('flex-1 overflow-y-auto px-1.5 py-1', className)}>
      <div className="relative w-full" style={{ height: `${virtualizer.getTotalSize()}px` }}>
        {virtualizer.getVirtualItems().map((row) => {
          const space = spaces[row.index]
          const isSelected = !multi && selectedId === space.id
          const isChecked = checked.has(space.id)
          return (
            <div
              key={space.id}
              data-index={row.index}
              ref={virtualizer.measureElement}
              className="group absolute top-0 left-0 w-full px-0.5 py-0.5"
              style={{ transform: `translateY(${row.start}px)` }}
            >
              <button
                type="button"
                onClick={() => (multi ? onToggle?.(space) : onSelect?.(space))}
                className={cn(
                  'flex w-full items-center gap-2 rounded-lg border border-transparent px-2 py-1.5 text-left transition-colors',
                  'hover:bg-accent/60',
                  isSelected && 'border-sky-500/30 bg-sky-500/10',
                )}
              >
                {multi ? (
                  <Checkbox
                    checked={isChecked}
                    tabIndex={-1}
                    aria-label={`選取 ${space.displayName}`}
                    className="pointer-events-none shrink-0"
                  />
                ) : space.type === 'SPACE' ? (
                  <HashIcon className="size-3.5 shrink-0 text-muted-foreground" />
                ) : (
                  <UserIcon className="size-3.5 shrink-0 text-muted-foreground" />
                )}

                <span className="min-w-0 flex-1">
                  <span
                    className={cn(
                      'block truncate text-xs font-medium',
                      isSelected ? 'text-sky-500' : 'text-foreground',
                    )}
                  >
                    {space.displayName || space.id}
                  </span>
                  <span className="flex items-center gap-1 truncate text-[10px] text-muted-foreground">
                    {/* 釘選的排在最前面，這個標記就是排序的解釋 */}
                    {space.pinned ? (
                      <>
                        <PinIcon className="size-2.5 shrink-0" aria-hidden />
                        <span className="sr-only">已釘選，</span>
                      </>
                    ) : null}
                    {spaceTypeLabel(space.type)} · 最後活動 {relativeTime(space.lastActiveTime)}
                  </span>
                </span>
              </button>

              {/* 改名鈕放在項目 button 的**外面**——HTML 不允許 button 巢狀，
                  放進去瀏覽器會把 DOM 拆掉。用 absolute 疊在右側。 */}
              {onRename && space.renamable && !multi ? (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    onRename(space)
                  }}
                  title={space.nameSource === 'dm_manual' ? '改這個名字' : '這個名字是猜的，可以自己取'}
                  aria-label={`為 ${space.displayName} 取名`}
                  className={cn(
                    'absolute top-1/2 right-2 -translate-y-1/2 rounded p-1 text-muted-foreground',
                    'opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100',
                    'hover:bg-accent hover:text-foreground',
                  )}
                >
                  <PencilIcon className="size-3" />
                </button>
              ) : null}
            </div>
          )
        })}
      </div>
    </div>
  )
}
