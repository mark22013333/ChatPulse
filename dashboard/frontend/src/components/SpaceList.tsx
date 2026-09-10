import { useLayoutEffect, useRef, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { HashIcon, Loader2Icon, PencilIcon, PinIcon, UserIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { relativeTime, spaceTypeLabel } from '@/lib/format'
import { nextListIndex } from '@/lib/listNavigation'
import { Checkbox } from '@/components/ui/checkbox'
import type { Space } from '@/lib/types'

interface SpaceListProps {
  spaces: Space[]
  loading?: boolean
  emptyHint?: string
  /** listbox 的可及名稱。清單本身要說得出自己是什麼。 */
  label?: string
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
 *
 * 鍵盤導航是 **roving tabindex**（規格 §10.5）：整份清單只占一個 Tab 停留點，
 * 進去之後用 ↑↓ Home End PageUp PageDown 走、Enter 選。
 *
 * **刻意不用 `aria-activedescendant`**：它要求被指向的節點常駐 DOM，而這裡
 * 只掛可視範圍 ＋ overscan 12 列，active 一捲出去多數輔助技術就失去目標。
 */
export function SpaceList({
  spaces,
  loading = false,
  emptyHint = '查無符合的 Space',
  label = 'Space 清單',
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

  const [activeIndex, setActiveIndex] = useState(0)
  // 搜尋過濾後清單會變短，上一輪停的位置可能已經超出範圍
  const active = Math.min(activeIndex, Math.max(spaces.length - 1, 0))

  // 移動之後才取焦。虛擬清單的目標列可能還沒掛出來（scrollToIndex 要等下一次
  // render），所以這個 effect 不設相依、每次 render 都試一次，取到就收手。
  const focusPending = useRef(false)
  useLayoutEffect(() => {
    if (!focusPending.current) return
    const target = parentRef.current?.querySelector<HTMLElement>(
      `[data-option-index="${active}"]`,
    )
    if (!target) return
    target.focus()
    focusPending.current = false
  })

  const moveTo = (index: number) => {
    setActiveIndex(index)
    focusPending.current = true
    // align 用 'auto'：只在目標不在可視範圍時才捲，捲動幅度最小。
    // 'center' 會讓每一次 ↓ 都把清單重新置中，看起來像整份清單在跳。
    virtualizer.scrollToIndex(index, { align: 'auto' })
  }

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Enter' || event.key === ' ') {
      const space = spaces[active]
      if (!space) return
      event.preventDefault()
      if (multi) onToggle?.(space)
      else onSelect?.(space)
      return
    }
    const next = nextListIndex(event.key, active, spaces.length)
    if (next === null) return // 不是導航鍵就交還給瀏覽器（Tab、Escape、打字）
    event.preventDefault()
    moveTo(next)
  }

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

  const rows = virtualizer.getVirtualItems()
  // roving tabindex 的陷阱：active 那一列若已經捲出可視範圍就不在 DOM 裡，
  // 於是整份清單沒有任何 tabIndex=0 的節點、Tab 進不來。這時把 tab 停留點
  // 讓給第一個掛著的列。
  const tabbable = rows.some((row) => row.index === active) ? active : (rows[0]?.index ?? 0)

  return (
    <div ref={parentRef} className={cn('flex-1 overflow-y-auto px-1.5 py-1', className)}>
      <ul
        role="listbox"
        aria-label={label}
        aria-multiselectable={multi || undefined}
        onKeyDown={onKeyDown}
        className="relative w-full"
        style={{ height: `${virtualizer.getTotalSize()}px` }}
      >
        {rows.map((row) => {
          const space = spaces[row.index]
          const isSelected = !multi && selectedId === space.id
          const isChecked = checked.has(space.id)
          return (
            <li
              // 改名鈕不能放進 role="option" 裡（option 內不該有可互動元素），
              // 所以 li 只當定位容器、不擔任何語意，option 是它裡面那一層。
              role="none"
              key={space.id}
              data-index={row.index}
              ref={virtualizer.measureElement}
              className="group absolute top-0 left-0 w-full px-0.5 py-0.5"
              style={{ transform: `translateY(${row.start}px)` }}
            >
              <div
                role="option"
                data-option-index={row.index}
                aria-selected={multi ? isChecked : isSelected}
                tabIndex={row.index === tabbable ? 0 : -1}
                onClick={() => {
                  setActiveIndex(row.index)
                  if (multi) onToggle?.(space)
                  else onSelect?.(space)
                }}
                // 焦點靠 Tab 移進來時（不是靠方向鍵），把 active 同步到它身上，
                // 否則接著按 ↓ 會從別的位置開始走
                onFocus={() => setActiveIndex(row.index)}
                className={cn(
                  'flex w-full cursor-default items-center gap-2 rounded-lg border border-transparent px-2 py-1.5 text-left transition-colors',
                  'hover:bg-accent/60',
                  isSelected && 'border-signal-line bg-signal-wash',
                )}
              >
                {multi ? (
                  // 勾勾只是畫面上的指示，語意由 option 的 aria-selected 承擔。
                  // 讓它同時是 role="checkbox" 會變成 option 裡巢了一個可選取
                  // 的東西，螢幕閱讀器會念兩次選取狀態。
                  <Checkbox
                    checked={isChecked}
                    tabIndex={-1}
                    aria-hidden
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
                      isSelected ? 'text-signal' : 'text-foreground',
                    )}
                  >
                    {space.displayName || space.id}
                  </span>
                  <span className="flex items-center gap-1 truncate text-2xs text-muted-foreground">
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
              </div>

              {/* 改名鈕疊在右側，是 option 的兄弟而不是子孫。 */}
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
            </li>
          )
        })}
      </ul>
    </div>
  )
}
