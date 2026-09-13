import {
  BarChart3Icon,
  CalendarRangeIcon,
  ChevronsLeftIcon,
  ChevronsRightIcon,
  InboxIcon,
  LogOutIcon,
  SettingsIcon,
  SparklesIcon,
  type LucideIcon,
} from 'lucide-react'
import { PulseMark } from '@/app/PulseMark'
import { ThemeToggle } from '@/components/ThemeToggle'
import { NAV_GROUPS, NAV_MODULES, type ModuleId, type NavModule } from '@/lib/modules'
import { hashForMentions, hashForSummary } from '@/lib/route'
import { cn } from '@/lib/utils'
import { useRouter } from '@/router/useRouter'
import { useAuthStore } from '@/store/auth'
import { useDraftStore } from '@/store/draft'
import { useMentionsStore } from '@/store/mentions'
import { useSpacesStore } from '@/store/spaces'
import { useSummaryStore } from '@/store/summary'
import { useUiStore } from '@/store/ui'

/**
 * 左側主導覽。
 *
 * ### 為什麼是 `<a>` 而不是 `<button>`
 *
 * 規格 §10.2 明訂工作台切換要用 `<nav>` + `<a href="#/...">` + `aria-current="page"`，
 * 而且**不要補 tablist 語意**——它本來就是連結。改版前的頂列頁籤是
 * `<button onClick>`，偏離了規格；順手改回來。附帶好處是中鍵開新分頁、
 * 複製連結這些原生行為都回來了（所以 onClick 只攔沒有修飾鍵的左鍵）。
 *
 * ### 收合態沒有文字標籤這題怎麼解
 *
 * 不繞路：**圖示下方直接放兩字中文標籤**（摘要／收件／統計／週報／設定），
 * 56px 塞得下。`title=` tooltip 是被 §17 紅線第 8 條禁止的，而且對鍵盤與
 * 觸控使用者不可達。展開時換成全名、改成水平排列。
 *
 * ### 三態的展開狀態
 *
 * `navExpanded === null` 時**不寫死寬度**，交給 CSS 斷點（≥1536 展開）。
 * 手動切換過才固定住。少了 null 這一態，沒表示過意見的人就會被迫接受
 * 某一種預設——而這個 app 在 1440 與 1920 下的合理預設本來就不同。
 */

const ICONS: Record<ModuleId, LucideIcon> = {
  summary: SparklesIcon,
  mentions: InboxIcon,
  stats: BarChart3Icon,
  weekly: CalendarRangeIcon,
  settings: SettingsIcon,
}

/** 三態 → 各處要套的 class。集中在這裡，免得散落各處對不起來。 */
function layoutClasses(navExpanded: boolean | null) {
  const auto = navExpanded === null
  const open = navExpanded === true
  return {
    auto,
    open,
    /** 導覽容器寬度 */
    width: auto ? 'w-14 2xl:w-nav' : open ? 'w-nav' : 'w-14',
    /** 只在展開態出現（全名標籤、分組標題、按鈕文字） */
    onlyOpen: auto ? 'hidden 2xl:inline' : open ? 'inline' : 'hidden',
    onlyOpenBlock: auto ? 'hidden 2xl:block' : open ? 'block' : 'hidden',
    /** 只在收合態出現（圖示下方的兩字標籤） */
    onlyCollapsed: auto ? 'inline 2xl:hidden' : open ? 'hidden' : 'inline',
    /** 項目本身的排列方向：收合是直的、展開是橫的 */
    itemFlow: auto
      ? 'flex-col gap-0.5 2xl:flex-row 2xl:gap-2'
      : open
        ? 'flex-row gap-2'
        : 'flex-col gap-0.5',
    /**
     * 未處理數字的位置：收合態沒有橫向空間，疊在圖示右上角；展開態排到最右。
     *
     * **只渲染一顆**、用 CSS 換位置，不要收合／展開各畫一顆——DOM 裡有兩顆
     * 同樣的 badge 會讓 E2E 的 `span.rounded-full` 一次選中兩個而撞上
     * Playwright 的 strict mode。
     */
    badgePlace: auto
      ? 'absolute top-0.5 right-1 2xl:static 2xl:ml-auto'
      : open
        ? 'ml-auto'
        : 'absolute top-0.5 right-1',
  }
}

export function SideNav() {
  const { route, navigate } = useRouter()
  const logout = useAuthStore((s) => s.logout)

  const selectedSpaceId = useSpacesStore((s) => s.selectedId)
  const selectedMentionId = useMentionsStore((s) => s.selectedId)
  const pendingCount = useMentionsStore((s) => s.counts.pending)

  // 訂閱布林值而不是內容：只有開始／結束生成時才重繪，不會被串流帶著跑
  const summaryStreaming = useSummaryStore((s) => s.streaming)
  const draftStreaming = useDraftStore((s) => s.streaming)

  const navExpanded = useUiStore((s) => s.navExpanded)
  const setNavExpanded = useUiStore((s) => s.setNavExpanded)
  const L = layoutClasses(navExpanded)

  /** 回到那個模組時落在哪——保留上次看的 Space／Mention，不要跳回清單頂端 */
  const hrefFor = (module: NavModule): string => {
    if (module.id === 'summary') return hashForSummary(selectedSpaceId)
    if (module.id === 'mentions') return hashForMentions(selectedMentionId)
    return module.hash
  }

  const busyOf = (id: ModuleId) =>
    (id === 'summary' && summaryStreaming) || (id === 'mentions' && draftStreaming)

  const brandHref = hashForSummary(selectedSpaceId)

  return (
    <nav
      aria-label="主導覽"
      className={cn(
        'flex shrink-0 flex-col border-r border-border bg-surface transition-[width] duration-150',
        L.width,
      )}
    >
      <a
        href={brandHref}
        onClick={(e) => {
          if (e.metaKey || e.ctrlKey || e.shiftKey) return
          e.preventDefault()
          navigate(brandHref)
        }}
        className="flex h-(--topbar-h) shrink-0 items-center gap-2 px-4 outline-none focus-visible:ring-2 focus-visible:ring-signal"
      >
        <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-signal-wash text-signal ring-1 ring-signal-line">
          <PulseMark className="size-3.5" />
        </span>
        <span className={cn('truncate text-sm font-semibold tracking-tight', L.onlyOpen)}>
          ChatPulse
        </span>
      </a>

      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto py-2">
        {NAV_GROUPS.map((group) => {
          const modules = NAV_MODULES.filter((m) => m.group === group)
          const allPlanned = modules.every((m) => m.status === 'planned')
          const headingId = `nav-group-${group}`
          return (
            <section key={group} aria-labelledby={headingId}>
              <h2 id={headingId} className={cn('px-3 pb-1 text-2xs text-fg-subtle', L.onlyOpenBlock)}>
                {group}
              </h2>
              {/* 整組都還沒上線就框起來，讓「這一區是預告」一眼看得出來。
                  虛線＝降級，與摘要章節的規線是同一套線型語意。 */}
              <ul
                className={cn(
                  'space-y-0.5 px-2',
                  allPlanned && 'mx-1.5 rounded-md border border-dashed border-line-strong px-1 py-1',
                )}
              >
                {modules.map((module) => (
                  <li key={module.id}>
                    <NavItem
                      module={module}
                      icon={ICONS[module.id]}
                      href={hrefFor(module)}
                      active={route.section === module.id}
                      badge={module.id === 'mentions' ? pendingCount : 0}
                      busy={busyOf(module.id)}
                      layout={L}
                      onNavigate={navigate}
                    />
                  </li>
                ))}
              </ul>
            </section>
          )
        })}
      </div>

      {/* 容器只留 p-1，按鈕自己 px-1——收合態總寬只有 56px，
          外層 p-2 加內層 px-2 會把可用寬吃到 24px，兩個中文字剛好卡邊界換行。 */}
      <div className="flex shrink-0 flex-col gap-0.5 border-t border-border p-1">
        <button
          type="button"
          onClick={() => setNavExpanded(!(L.open || (L.auto && window.innerWidth >= 1536)))}
          className={cn(
            'flex items-center justify-center rounded-md px-1 py-1.5 text-xs whitespace-nowrap text-fg-dim outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-signal',
            L.itemFlow,
          )}
        >
          {/* 圖示方向不能用 JS 判斷：auto 態下「現在到底是展開還是收合」由 CSS
              斷點決定，JS 這邊看到的 navExpanded 永遠是 null。所以兩個圖示都畫，
              用跟文字同一套 onlyOpen／onlyCollapsed 去顯示。 */}
          <ChevronsLeftIcon className={cn('size-4 shrink-0', L.onlyOpen)} />
          <ChevronsRightIcon className={cn('size-4 shrink-0', L.onlyCollapsed)} />
          <span className={cn('truncate', L.onlyOpen)}>收合</span>
          <span className={cn('text-2xs leading-none', L.onlyCollapsed)}>展開</span>
        </button>
        <ThemeToggle
          className={cn(
            'flex items-center justify-center rounded-md px-1 py-1.5 text-xs whitespace-nowrap text-fg-dim outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-signal',
            L.itemFlow,
          )}
          label={
            <>
              <span className={cn('truncate', L.onlyOpen)}>主題</span>
              <span className={cn('text-2xs leading-none whitespace-nowrap', L.onlyCollapsed)}>
                主題
              </span>
            </>
          }
        />
        <button
          type="button"
          onClick={() => void logout()}
          className={cn(
            'flex items-center justify-center rounded-md px-1 py-1.5 text-xs whitespace-nowrap text-fg-dim outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-signal',
            L.itemFlow,
          )}
        >
          <LogOutIcon className="size-4 shrink-0" />
          <span className={cn('truncate', L.onlyOpen)}>登出</span>
          <span className={cn('text-2xs leading-none', L.onlyCollapsed)}>登出</span>
        </button>
      </div>
    </nav>
  )
}

interface NavItemProps {
  module: NavModule
  icon: LucideIcon
  href: string
  active: boolean
  badge: number
  busy: boolean
  layout: ReturnType<typeof layoutClasses>
  onNavigate: (hash: string) => void
}

function NavItem({ module, icon: Icon, href, active, badge, busy, layout, onNavigate }: NavItemProps) {
  const planned = module.status === 'planned'

  const body = (
    <>
      {/* 當前項的 3px 實心條。收合態沒有文字可以加粗，位置全靠它指出來 */}
      <span
        aria-hidden
        className={cn(
          'absolute inset-y-1 left-0 w-[3px] rounded-r-sm',
          active ? 'bg-signal' : 'bg-transparent',
        )}
      />
      <Icon className={cn('size-4 shrink-0', planned && 'opacity-60')} />

      <span className={cn('truncate', layout.onlyOpen)}>{module.label}</span>
      {/* whitespace-nowrap 是必要的：收合態只有 56px，兩個中文字一旦被
          允許換行就會變成直排（底部按鈕實際踩過） */}
      <span className={cn('text-2xs leading-none whitespace-nowrap', layout.onlyCollapsed)}>
        {module.shortLabel}
      </span>

      {busy ? (
        <>
          <span className="live-dot" aria-hidden />
          <span className="sr-only">（正在生成，切到別的模組也會繼續）</span>
        </>
      ) : null}

      {badge > 0 ? (
        <span
          className={cn(
            'inline-flex min-w-4 items-center justify-center rounded-full bg-signal px-1 text-2xs font-semibold text-signal-on',
            layout.badgePlace,
          )}
        >
          {badge}
        </span>
      ) : null}
    </>
  )

  if (planned) {
    return (
      <span
        aria-disabled="true"
        className={cn(
          'relative flex cursor-default items-center justify-center rounded-md px-2 py-1.5 text-xs text-disabled-fg',
          layout.itemFlow,
        )}
      >
        {body}
        {/* 點線＝缺值，與摘要章節的線型語意一致。說明寫在畫面上，不進 tooltip */}
        <span
          className={cn(
            'ml-auto border-b border-dotted border-line-strong text-2xs',
            layout.onlyOpen,
          )}
        >
          {module.plannedNote}
        </span>
      </span>
    )
  }

  return (
    <a
      href={href}
      aria-current={active ? 'page' : undefined}
      onClick={(e) => {
        // 中鍵／⌘+click 交給瀏覽器原生行為，只攔沒有修飾鍵的左鍵
        if (e.metaKey || e.ctrlKey || e.shiftKey) return
        e.preventDefault()
        onNavigate(href)
      }}
      className={cn(
        'relative flex items-center justify-center rounded-md px-2 py-1.5 text-xs outline-none focus-visible:ring-2 focus-visible:ring-signal',
        layout.itemFlow,
        active
          ? 'bg-muted font-medium text-foreground'
          : 'text-fg-dim hover:bg-muted hover:text-foreground',
      )}
    >
      {body}
    </a>
  )
}
