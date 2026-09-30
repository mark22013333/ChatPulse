import { PanelRightIcon, SearchIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useEvidenceBundle } from '@/components/evidence/EvidenceColumn'
import { ThemeToggle } from '@/components/ThemeToggle'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { evidenceOriginOf } from '@/lib/evidence'
import { labelOf } from '@/lib/modules'
import { useRouter } from '@/router/useRouter'
import { useAuthStore } from '@/store/auth'
import { useUiStore } from '@/store/ui'

/**
 * 頂列：模組標題 ┊ 搜尋框(⌘K) ┊ 證據鈕 ┊ 深淺切換 ┊ Viewer
 * （2026-10-01 改版，規格 `2026-10-01-table-layout.md` §3）。
 *
 * ### 搜尋框是 CommandPalette 的入口，不是另一套搜尋
 *
 * 點它與按 ⌘K 開的是同一個面板。被否決：做一套頁內搜尋——兩套搜尋的結果
 * 會不一致（面板搜 Space、Mention 與設定，頁內搜尋只看得到當前模組）。
 *
 * ⚠️ 所以它**是 `<button>` 不是 `<input>`**，只是長得像輸入框。做成真的
 * input 就得把打進去的字轉交給面板、處理 IME 組字中途開面板、還要決定焦點
 * 該留在哪一個輸入框——三件事任何一件沒做好，中文輸入就會掉字。用 button
 * 還有一個免費的好處：面板關閉時會把焦點還給「開啟它的元素」（見
 * `CommandPalette` 的 restoreFocus），還回來的就是這顆，鍵盤使用者不會迷路。
 *
 * ### 頂列不放通知鈴
 *
 * 規格 §3：通知中心需要事件模型與已讀狀態，是新功能不是改版；放一個不會動的
 * 鈴等於佔位元件。Mention 數量已經在 SideNav 的徽章上。有測試守著這件事。
 *
 * ### 證據鈕為什麼還在
 *
 * 它不是通知，是 <1280 時打開證據抽屜的唯一入口（規格 2026-09-09 §12）。
 * 拿掉它，那個寬度就看不到「這份產出建立在什麼之上」。
 *
 * ### 麵包屑為什麼不顯示 Space 名稱
 *
 * 主內容區本來就有大標題，再寫一次是重複、不是層級。而且要拿到名稱得訂閱
 * 整份 436 筆的 Space 清單，頂列會被清單變動帶著重繪——現在它只訂閱布林
 * 與計數（規格 2026-09-09 §9.3）。自己用細 selector 讀 store，不從 AppShell 接 props。
 */
export function TopBar() {
  const { route } = useRouter()

  const me = useAuthStore((s) => s.me)
  const setPaletteOpen = useUiStore((s) => s.setPaletteOpen)

  const breakpoint = useBreakpoint()
  const evidenceInline = breakpoint === 'wide'
  const drawerOpen = useUiStore((s) => s.drawer[breakpoint] === true)
  const toggleDrawer = useUiStore((s) => s.toggleDrawer)

  // 抽屜關著時，證據鈕要讓人知道「值得打開看一眼」
  const { bundle } = useEvidenceBundle(evidenceOriginOf(route.section))
  const attentionCount = bundle.attentionCount

  const viewer = me?.viewer ?? null
  const viewerName = viewer ? viewer.display_name || viewer.email : ''

  return (
    <header className="flex h-(--topbar-h) shrink-0 items-center gap-3 border-b border-border bg-surface px-5">
      <h1 className="mr-auto truncate text-md font-semibold">{labelOf(route.section)}</h1>

      <button
        type="button"
        onClick={() => setPaletteOpen(true)}
        aria-haspopup="dialog"
        aria-keyshortcuts="Meta+K Control+K"
        className="flex h-8 w-72 min-w-0 shrink items-center gap-2 rounded-lg border border-line-strong bg-background px-2.5 text-xs text-fg-subtle hover:border-fg-subtle hover:text-fg-dim"
      >
        <SearchIcon className="size-4 shrink-0" aria-hidden />
        {/* 可見文字就是可及名稱的開頭（WCAG 2.5.3 label in name），
            不另掛 aria-label 蓋掉它——語音控制的人會照著畫面上的字念 */}
        <span className="truncate">
          搜尋 Space、Mention 與設定
          <span className="sr-only">（開啟命令面板）</span>
        </span>
        <kbd aria-hidden className="metric ml-auto shrink-0 text-2xs">
          ⌘K
        </kbd>
      </button>

      {!evidenceInline ? (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => toggleDrawer(breakpoint)}
          aria-expanded={drawerOpen}
          aria-label={drawerOpen ? '關閉證據欄' : '開啟證據欄'}
        >
          <PanelRightIcon />
          證據
          {/* 有降級項目時亮一個點，讓人知道值得打開看一眼 */}
          {attentionCount > 0 ? (
            <span className="bg-caution size-1.5 rounded-full" aria-hidden />
          ) : null}
          {attentionCount > 0 ? (
            <span className="sr-only">（有 {attentionCount} 項需要看一眼）</span>
          ) : null}
        </Button>
      ) : null}

      <ThemeToggle />

      {viewer ? (
        <div className="flex min-w-0 items-center gap-2 border-l border-border pl-3">
          <span
            aria-hidden
            className="flex size-7 shrink-0 items-center justify-center rounded-full bg-signal text-xs font-semibold text-signal-on"
          >
            {/* 取第一個字元而不是第一個 code unit：姓名開頭若是 emoji 或
                罕用字（surrogate pair），`[0]` 會切出半個字、畫成亂碼 */}
            {Array.from(viewerName)[0] ?? ''}
          </span>
          <span className="flex min-w-0 flex-col leading-tight">
            <span className="truncate text-xs font-medium">{viewerName}</span>
            {viewer.display_name ? (
              // 小於 1024 收掉 email：那個寬度頂列要讓給搜尋框，名字已經足夠辨識
              <span className="hidden truncate text-2xs text-fg-subtle lg:inline">
                {viewer.email}
              </span>
            ) : null}
          </span>
        </div>
      ) : null}
    </header>
  )
}
