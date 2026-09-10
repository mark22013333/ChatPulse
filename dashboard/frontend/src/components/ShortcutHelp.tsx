import { useEffect, useRef } from 'react'
import { XIcon } from 'lucide-react'
import { SHORTCUT_HELP } from '@/lib/shortcutHelp'
import { useUiStore } from '@/store/ui'

/**
 * 快捷鍵說明（`?`，設計規格 §11.1）。
 *
 * 為什麼這個面板是必要的、而不是「有空再做」：這一版一次補上十個快捷鍵，
 * 其中 `g s` 這種兩鍵序列沒有任何畫面線索。沒有一張讀得到的表，那些鍵等於
 * 只有寫的人會用——而 `?` 是這類介面的通用約定，使用者會去按它。
 *
 * 內容直接來自 `lib/shortcutHelp.ts` 的 `SHORTCUT_HELP`。那個檔的測試會拿
 * 每一列的 probe 逐條證明「表上寫的鍵真的解析得出動作」。
 *
 * ### Esc 的處理刻意與命令面板一致
 *
 * 監聽掛在 window 上並且 `preventDefault()` ＋ `stopPropagation()`。前者讓
 * 設定中心那個也掛在 window 上的監聽器看得到 `defaultPrevented`、不會被一起
 * 關掉（2026-09-10 由真瀏覽器 E2E 抓過同型的 bug）；後者擋住更外層。
 * 另外 `store/ui.ts` 的 `isOverlayOpen()` 會把 `helpOpen` 算進去，
 * 焦點跑到面板外面時設定同樣不會反應。
 */
export function ShortcutHelp() {
  const open = useUiStore((state) => state.helpOpen)
  const setOpen = useUiStore((state) => state.setHelpOpen)
  const panelRef = useRef<HTMLDivElement>(null)
  const restoreFocus = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) return
    restoreFocus.current = document.activeElement as HTMLElement | null
    panelRef.current?.focus()
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      setOpen(false)
      restoreFocus.current?.focus()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, setOpen])

  if (!open) return null

  const close = () => {
    setOpen(false)
    restoreFocus.current?.focus()
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 pt-[10vh]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close()
      }}
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="鍵盤快捷鍵"
        className="border-line bg-raised shadow-overlay flex max-h-[80vh] w-full max-w-lg flex-col overflow-hidden rounded-xl border outline-none"
      >
        <div className="border-line flex items-baseline gap-2 border-b px-gutter py-2.5">
          <h2 className="text-sm font-semibold">鍵盤快捷鍵</h2>
          {/* 平台差異寫成一行可見文字，而不是去偵測 navigator：偵測錯了
              使用者只會看到一個按不動的鍵，而這句話兩個平台都讀得懂 */}
          <p className="text-fg-dim text-2xs">⌘ 在 Windows／Linux 是 Ctrl</p>
          <button
            type="button"
            onClick={close}
            aria-label="關閉快捷鍵說明"
            className="text-fg-subtle hover:bg-accent hover:text-foreground ml-auto shrink-0 rounded p-1"
          >
            <XIcon className="size-3.5" />
          </button>
        </div>

        <dl className="min-h-0 flex-1 overflow-y-auto px-gutter py-2">
          {SHORTCUT_HELP.map((row) => (
            <div
              key={row.keys.join('+') + row.label}
              className="flex items-baseline gap-3 py-1.5"
            >
              <dt className="flex shrink-0 items-baseline gap-1">
                {row.keys.map((key, index) => (
                  <kbd
                    key={`${key}-${index}`}
                    className="border-line bg-background text-fg-subtle text-2xs min-w-5 rounded border px-1.5 py-0.5 text-center font-medium"
                  >
                    {key}
                  </kbd>
                ))}
              </dt>
              <dd className="min-w-0 flex-1 text-xs leading-relaxed">
                {row.label}
                {/* 「這個鍵只在清單上有焦點時有效」是決定它能不能用的資訊，
                    不是補充說明，所以放在可見文字裡（規格 §10.3） */}
                {row.scope === 'list' ? (
                  <span className="text-fg-dim">　（焦點在左欄清單上時）</span>
                ) : null}
              </dd>
            </div>
          ))}
        </dl>

        <p className="border-line text-fg-dim px-gutter border-t py-2 text-2xs leading-relaxed">
          送出回話與推播回 Google Chat 刻意沒有快捷鍵，也不在命令面板裡——
          不可撤回的動作不該有肌肉記憶。
        </p>
      </div>
    </div>
  )
}
