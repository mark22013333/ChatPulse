import { PanelRightIcon, SearchIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useEvidenceBundle } from '@/components/evidence/EvidenceColumn'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { labelOf } from '@/lib/modules'
import { useRouter } from '@/router/useRouter'
import { useAuthStore } from '@/store/auth'
import { useUiStore } from '@/store/ui'

/**
 * 頂列：當前位置 + 全域動作。
 *
 * ### 這裡曾經有什麼、為什麼搬走
 *
 * 改版前它擠了七樣東西：品牌、兩顆工作台切換膠囊、⌘K、證據鈕、設定、主題、
 * 登出。頂列橫向空間有限，工作台一多就放不下——那正是這次改成左側導覽的
 * 直接原因。品牌／切換／設定／主題／登出都搬進 `SideNav`（那裡是垂直的，
 * 加模組只是陣列多一筆），頂列只留兩件事：**你在哪裡**，以及**跟當前位置
 * 無關的全域動作**。
 *
 * ### 麵包屑為什麼不顯示 Space 名稱
 *
 * 主內容區本來就有大標題（「李姿誼Sica Lee」＋ space id），麵包屑再寫一次
 * 是重複、不是層級。而且要拿到名稱得訂閱整份 436 筆的 Space 清單，
 * 頂列會被清單變動帶著重繪——現在它只訂閱布林與計數（規格 §9.3）。
 *
 * 自己用細 selector 讀 store，不從 AppShell 接 props。
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
  const { bundle } = useEvidenceBundle(route.section === 'mentions' ? 'draft' : 'summary')
  const attentionCount = bundle.attentionCount

  return (
    <header className="flex h-(--topbar-h) shrink-0 items-center gap-3 border-b border-border px-4">
      <h1 className="truncate text-sm font-medium">{labelOf(route.section)}</h1>

      <div className="ml-auto flex items-center gap-2">
        {me?.viewer ? (
          <span className="hidden text-xs text-muted-foreground sm:inline">
            {me.viewer.display_name || me.viewer.email}
          </span>
        ) : null}
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setPaletteOpen(true)}
          aria-label="開啟命令面板"
        >
          <SearchIcon />
          <kbd className="text-fg-subtle text-2xs">⌘K</kbd>
        </Button>
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
      </div>
    </header>
  )
}
