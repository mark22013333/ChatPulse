import { type ReactNode } from 'react'
import {
  InboxIcon,
  LogOutIcon,
  PanelRightIcon,
  SearchIcon,
  SettingsIcon,
  SparklesIcon,
} from 'lucide-react'
import { PulseMark } from '@/app/PulseMark'
import { Button } from '@/components/ui/button'
import { ThemeToggle } from '@/components/ThemeToggle'
import { useEvidenceBundle } from '@/components/evidence/EvidenceColumn'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { hashForMentions, hashForSettings, hashForSummary } from '@/lib/route'
import { cn } from '@/lib/utils'
import { useRouter } from '@/router/useRouter'
import { useAuthStore } from '@/store/auth'
import { useDraftStore } from '@/store/draft'
import { useMentionsStore } from '@/store/mentions'
import { useSpacesStore } from '@/store/spaces'
import { useSummaryStore } from '@/store/summary'
import { useUiStore } from '@/store/ui'

/**
 * 頂列（規格 §9.2）：脈搏識別、工作台切換、⌘K、證據鈕、設定、主題、登出。
 *
 * 自己用細 selector 讀 store（規格 §9.3），不從 AppShell 接 props——頂列訂閱
 * 的都是布林與計數，只有真的變化時才重繪，不會被串流帶著跑。
 */
export function TopBar() {
  const { route, navigate } = useRouter()
  const view = route.section === 'mentions' ? 'mentions' : 'summary'

  const me = useAuthStore((s) => s.me)
  const logout = useAuthStore((s) => s.logout)

  const selectedSpaceId = useSpacesStore((s) => s.selectedId)
  const selectedMentionId = useMentionsStore((s) => s.selectedId)
  const pendingCount = useMentionsStore((s) => s.counts.pending)

  // 讓頁籤能顯示「另一邊還在生成」。訂閱的是布林值，只有開始／結束時才變，
  // 不會每個 chunk 都讓頂列重繪。
  const summaryStreaming = useSummaryStore((s) => s.streaming)
  const draftStreaming = useDraftStore((s) => s.streaming)

  const setPaletteOpen = useUiStore((s) => s.setPaletteOpen)
  const breakpoint = useBreakpoint()
  const evidenceInline = breakpoint === 'wide'
  const drawerOpen = useUiStore((s) => s.drawer[breakpoint] === true)
  const toggleDrawer = useUiStore((s) => s.toggleDrawer)

  // 抽屜關著時，證據鈕要讓人知道「值得打開看一眼」
  const { bundle } = useEvidenceBundle(view === 'mentions' ? 'draft' : 'summary')
  const attentionCount = bundle.attentionCount

  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-border px-4">
      <div className="flex items-center gap-2">
        <span className="flex size-6 items-center justify-center rounded-md bg-signal-wash text-signal ring-1 ring-signal-line">
          <PulseMark className="size-3.5" />
        </span>
        <span className="text-sm font-semibold tracking-tight">ChatPulse</span>
      </div>

      <nav aria-label="工作台" className="ml-2 flex items-center gap-1 rounded-lg bg-muted p-0.5">
        <ViewTab
          active={view === 'summary'}
          onClick={() => navigate(hashForSummary(selectedSpaceId))}
          icon={<SparklesIcon className="size-3.5" />}
          label="摘要工作台"
          busy={summaryStreaming}
        />
        <ViewTab
          active={view === 'mentions'}
          onClick={() => navigate(hashForMentions(selectedMentionId))}
          icon={<InboxIcon className="size-3.5" />}
          label="Mention 收件匣"
          badge={pendingCount}
          busy={draftStreaming}
        />
      </nav>

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
        <Button
          size="sm"
          variant="ghost"
          onClick={() => navigate(hashForSettings('reply'))}
          aria-label="設定"
        >
          <SettingsIcon />
          設定
        </Button>
        <ThemeToggle />
        <Button size="sm" variant="ghost" onClick={() => void logout()}>
          <LogOutIcon />
          登出
        </Button>
      </div>
    </header>
  )
}

interface ViewTabProps {
  active: boolean
  onClick: () => void
  icon: ReactNode
  label: string
  badge?: number
  /** 這個工作台正在跑 AI 生成。切走之後仍會繼續，用一個脈動點讓人知道。 */
  busy?: boolean
}

function ViewTab({ active, onClick, icon, label, badge, busy }: ViewTabProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
        active
          ? 'bg-background text-foreground shadow-sm'
          : 'text-muted-foreground hover:text-foreground',
      )}
    >
      {icon}
      {label}
      {busy ? (
        <>
          {/* 常態與 reduced-motion 的降級表徵都定義在 index.css 的 .live-dot，
              不再逐處寫 motion-safe:（那樣一定會漏）。 */}
          <span className="live-dot" aria-hidden />
          {/* 說明改成頁籤可及名稱的一部分，不放進 tooltip——那對鍵盤與
              觸控使用者不可達（設計規格 §10.2）。 */}
          <span className="sr-only">（正在生成，切到別的頁籤也會繼續）</span>
        </>
      ) : null}
      {badge && badge > 0 ? (
        <span className="inline-flex min-w-4 items-center justify-center rounded-full bg-signal px-1 text-2xs font-semibold text-signal-on">
          {badge}
        </span>
      ) : null}
    </button>
  )
}
