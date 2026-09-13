import { useEffect, useMemo, useRef, useState } from 'react'
import { SearchIcon } from 'lucide-react'
import { buildCommands, filterCommands, type Command } from '@/lib/commands'
import { relativeTime, spaceTypeLabel } from '@/lib/format'
import { isComposing } from '@/lib/keyboard'
import { cn } from '@/lib/utils'
import { useRouter } from '@/router/useRouter'
import { useMentionsStore } from '@/store/mentions'
import { useSpacesStore } from '@/store/spaces'
import { useUiStore } from '@/store/ui'

/**
 * 命令面板（設計規格 §11）。
 *
 * 存在的理由是 436 個 Space：不必先找到清單、不必捲動，打三個字就到。
 *
 * **送出回話與推播不在這裡**（`lib/commands.ts` 有測試守著）：不可撤回的
 * 動作不該有肌肉記憶，而「打字 → Enter」正是最容易誤觸的介面。
 */
export function CommandPalette() {
  const open = useUiStore((state) => state.paletteOpen)
  const setOpen = useUiStore((state) => state.setPaletteOpen)
  const { navigate } = useRouter()

  const spaces = useSpacesStore((state) => state.items)
  const mentions = useMentionsStore((state) => state.items)

  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const restoreFocus = useRef<HTMLElement | null>(null)

  const all = useMemo(
    () => buildCommands({ spaces, mentions, spaceTypeLabel, relativeTime }),
    [spaces, mentions],
  )
  const results = useMemo(() => filterCommands(all, query), [all, query])

  // 每次開啟都從乾淨狀態開始，並記住原本的焦點在哪
  useEffect(() => {
    if (!open) return
    restoreFocus.current = document.activeElement as HTMLElement | null
    setQuery('')
    setActive(0)
    // 等 DOM 掛上再聚焦
    const id = window.setTimeout(() => inputRef.current?.focus(), 0)
    return () => window.clearTimeout(id)
  }, [open])

  useEffect(() => setActive(0), [query])

  // 高亮項捲進可視範圍。用 aria-activedescendant 而不是移動真實焦點——
  // 焦點要留在輸入框，不然打字會中斷。
  useEffect(() => {
    if (!open) return
    listRef.current
      ?.querySelector(`[data-index="${active}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [active, open])

  const close = () => {
    setOpen(false)
    restoreFocus.current?.focus()
  }

  const run = (command: Command) => {
    navigate(command.href)
    close()
  }

  if (!open) return null

  const onKeyDown = (event: React.KeyboardEvent) => {
    // 注音選字時按 Enter 是「選這個字」，不是「執行這個指令」
    if (isComposing(event)) return

    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActive((i) => (results.length ? (i + 1) % results.length : 0))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((i) => (results.length ? (i - 1 + results.length) % results.length : 0))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const command = results[active]
      if (command) run(command)
    } else if (event.key === 'Escape') {
      event.preventDefault()
      close()
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 pt-[12vh]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="命令面板"
        className="border-line bg-raised shadow-overlay w-full max-w-lg overflow-hidden rounded-xl border"
      >
        <div className="border-line flex items-center gap-2 border-b px-gutter py-2.5">
          <SearchIcon className="text-fg-subtle size-4 shrink-0" aria-hidden />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder={`搜尋 ${spaces.length} 個 Space、Mention 與設定…`}
            aria-label="搜尋指令"
            // 這是 ARIA 的 combobox 模式（輸入框 ＋ 一份 listbox 彈出清單），
            // 所以 role 與 aria-expanded 都要明說。少了 role="combobox"，
            // 下面那個 aria-activedescendant 在多數螢幕閱讀器上不會被採用
            // ——高亮移動就完全念不出來。面板只在開著時渲染，所以 expanded
            // 恆為 true。
            role="combobox"
            aria-expanded
            aria-controls="command-results"
            aria-activedescendant={results[active] ? `command-${results[active].id}` : undefined}
            className="text-foreground placeholder:text-fg-subtle min-w-0 flex-1 bg-transparent text-sm outline-none"
          />
          <kbd className="text-fg-subtle text-2xs">Esc</kbd>
        </div>

        <ul
          ref={listRef}
          id="command-results"
          role="listbox"
          aria-label="搜尋結果"
          className="max-h-80 overflow-y-auto py-1"
        >
          {results.length === 0 ? (
            <li className="text-fg-dim px-gutter py-6 text-center text-sm">
              找不到「{query}」。試試 Space 名稱、提到你的人，或「設定」。
            </li>
          ) : (
            results.map((command, index) => (
              <li
                key={command.id}
                id={`command-${command.id}`}
                data-index={index}
                role="option"
                aria-selected={index === active}
                onMouseEnter={() => setActive(index)}
                onMouseDown={(event) => {
                  event.preventDefault()
                  run(command)
                }}
                className={cn(
                  'mx-1 flex cursor-pointer items-baseline gap-2 rounded-lg px-2.5 py-2',
                  index === active && 'bg-signal-wash',
                )}
              >
                <span className="text-fg-subtle text-2xs w-14 shrink-0">{command.group}</span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm">{command.label}</span>
                  {command.hint ? (
                    <span className="text-fg-dim text-2xs block truncate">{command.hint}</span>
                  ) : null}
                </span>
                {command.pinned ? (
                  <span className="text-signal text-2xs shrink-0">已釘選</span>
                ) : null}
              </li>
            ))
          )}
        </ul>
      </div>
    </div>
  )
}
