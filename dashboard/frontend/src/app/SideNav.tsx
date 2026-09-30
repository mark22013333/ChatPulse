import {
  BarChart3Icon,
  CalendarRangeIcon,
  ClockIcon,
  InboxIcon,
  LogOutIcon,
  SettingsIcon,
  SparklesIcon,
  type LucideIcon,
} from 'lucide-react'
import { PulseMark } from '@/app/PulseMark'
import { NAV_GROUPS, NAV_MODULES, type ModuleId, type NavModule } from '@/lib/modules'
import { hashForMentions, hashForSummary } from '@/lib/route'
import { cn } from '@/lib/utils'
import { useRouter } from '@/router/useRouter'
import { useAuthStore } from '@/store/auth'
import { useDraftStore } from '@/store/draft'
import { useMentionsStore } from '@/store/mentions'
import { useSpacesStore } from '@/store/spaces'
import { useSummaryStore } from '@/store/summary'

/**
 * 左側主導覽：深藍側欄（2026-10-01 改版，規格 `2026-10-01-table-layout.md` §3）。
 *
 * ### 為什麼是 `<a>` 而不是 `<button>`
 *
 * 規格 §10.2 明訂工作台切換要用 `<nav>` + `<a href="#/...">` + `aria-current="page"`，
 * 而且**不要補 tablist 語意**——它本來就是連結。附帶好處是中鍵開新分頁、
 * 複製連結這些原生行為都在（所以 onClick 只攔沒有修飾鍵的左鍵）。
 * 改版草稿用的是 `<button>`；那是示意用的 HTML，不照抄。
 *
 * ### 固定寬度，不做收合
 *
 * 在此之前這裡有「收合／展開／跟著斷點走」三態（`useUiStore().navExpanded`）。
 * 這次拿掉，理由照規格 §3：主體從三欄變單欄後寬度已經充裕；只有 6 個模組，
 * 另有 `g s`／`g m` 快速切換；收合要多背 tooltip、焦點、偏好儲存三份測試，
 * 換來的價值不夠。
 *
 * ⚠️ store 裡的 `navExpanded`／`setNavExpanded` **刻意沒刪**（規格 2026-09-09
 * §17 紅線 3：既有 store 只准加、不准刪）。它現在沒有讀者，localStorage 裡
 * 舊的 `chatpulse.navExpanded` 也無害——讀了不會影響任何畫面。
 *
 * ### 主題切換搬到頂列
 *
 * 規格 §3 的骨架把「深淺切換」放在頂列右側、Viewer 旁邊。這裡只留登出。
 *
 * ### 顏色為什麼全是 `nav-*`
 *
 * 側欄在淺色主題下也是深色面，吃 `--foreground`／`--signal` 會直接失去對比
 * （全域 focus 環落在深藍上只有 2.06:1）。外層掛 `nav-surface`，index.css 會
 * 把這個範圍內的 focus 環換成 `--nav-indicator`。理由與數字在 index.css。
 */

const ICONS: Record<ModuleId, LucideIcon> = {
  summary: SparklesIcon,
  mentions: InboxIcon,
  // 時鐘而不是日曆：日曆已經被「週報」用掉了，兩個模組在導航上相鄰，
  // 圖示再撞就分不出來
  worklog: ClockIcon,
  stats: BarChart3Icon,
  weekly: CalendarRangeIcon,
  settings: SettingsIcon,
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
      className="nav-surface flex w-nav shrink-0 flex-col bg-nav text-nav-fg"
    >
      <a
        href={brandHref}
        onClick={(e) => {
          if (e.metaKey || e.ctrlKey || e.shiftKey) return
          e.preventDefault()
          navigate(brandHref)
        }}
        // 高度跟頂列同一個 token：品牌區下緣與頂列下緣要對成一條水平線
        className="flex h-(--topbar-h) shrink-0 items-center gap-2.5 border-b border-nav-line px-4 outline-none"
      >
        <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-nav-active text-nav-indicator">
          <PulseMark className="size-4" />
        </span>
        <span className="flex min-w-0 flex-col">
          <span className="truncate text-md leading-tight font-semibold tracking-tight text-nav-fg-active">
            ChatPulse
          </span>
          <span className="truncate text-2xs text-nav-fg-dim">Google Chat 輔助工具</span>
        </span>
      </a>

      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto py-3">
        {NAV_GROUPS.map((group) => {
          const modules = NAV_MODULES.filter((m) => m.group === group)
          const allPlanned = modules.every((m) => m.status === 'planned')
          const headingId = `nav-group-${group}`
          return (
            <section key={group} aria-labelledby={headingId}>
              <h2
                id={headingId}
                className="px-4 pt-1 pb-1 text-2xs font-medium tracking-wider text-nav-fg-dim"
              >
                {group}
              </h2>
              {/* 整組都還沒上線就框起來，讓「這一區是預告」一眼看得出來。
                  虛線＝降級，與摘要章節的規線是同一套線型語意。 */}
              <ul
                className={cn(
                  'space-y-0.5',
                  allPlanned && 'mx-2 rounded-md border border-dashed border-nav-fg-dim/50 py-1',
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
                      onNavigate={navigate}
                    />
                  </li>
                ))}
              </ul>
            </section>
          )
        })}
      </div>

      <div className="shrink-0 border-t border-nav-line p-2">
        <button
          type="button"
          onClick={() => void logout()}
          className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-xs text-nav-fg-dim outline-none hover:bg-nav-active/50 hover:text-nav-fg"
        >
          <LogOutIcon className="size-4 shrink-0" />
          登出
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
  onNavigate: (hash: string) => void
}

/** 項目共用的版型。左邊留 3px 給當前項色條，所以 padding 從 pl-4 起算。 */
const ITEM = 'relative flex items-center gap-2.5 py-2 pr-3 pl-4 text-sm outline-none'

function NavItem({ module, icon: Icon, href, active, badge, busy, onNavigate }: NavItemProps) {
  const planned = module.status === 'planned'

  if (planned) {
    return (
      // 尚未上線：不是連結，也不可聚焦。說明寫在畫面上，不進 tooltip（§17 紅線 8）。
      // 字色用 nav-fg-dim 而不是 disabled-fg：後者是為淺色表面配的，
      // 放到深藍底上會掉到 2:1 以下，連「這裡有東西」都讀不出來。
      <span aria-disabled="true" className={cn(ITEM, 'cursor-default items-start text-nav-fg-dim')}>
        <Icon className="mt-0.5 size-4 shrink-0 opacity-60" />
        {/* 說明排在名稱**下方**而不是同一行靠右：「回顧與匯出，不寫回工時系統」
            在 200px 寬裡放不下，同一行時會把名稱擠掉、還把整欄撐出橫向捲軸
            （2026-10-01 截圖實測）。改成兩行之後名稱永遠看得到。 */}
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="truncate">{module.label}</span>
          {/* 點線＝缺值，與摘要章節的線型語意一致 */}
          <span className="self-start border-b border-dotted border-nav-fg-dim text-2xs leading-snug">
            {module.plannedNote}
          </span>
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
        ITEM,
        active
          ? 'bg-nav-active font-medium text-nav-fg-active'
          : 'text-nav-fg hover:bg-nav-active/50 hover:text-nav-fg-active',
      )}
    >
      {/* 當前項的 3px 實心條。不能只靠底色：nav 與 nav-active 的明度差只有
          0.08，在投影機或低亮度螢幕上會糊在一起，色條（對 nav-active 4.58:1）
          才是那條過得了 1.4.11 的線索 */}
      <span
        aria-hidden
        className={cn(
          'absolute inset-y-0 left-0 w-[3px]',
          active ? 'bg-nav-indicator' : 'bg-transparent',
        )}
      />
      <Icon className="size-4 shrink-0" />
      <span className="truncate">{module.label}</span>

      {busy ? (
        <>
          <span className="live-dot" aria-hidden />
          <span className="sr-only">（正在生成，切到別的模組也會繼續）</span>
        </>
      ) : null}

      {badge > 0 ? (
        <span className="metric ml-auto inline-flex min-w-5 items-center justify-center rounded-full bg-nav-badge px-1.5 text-2xs font-semibold text-nav">
          {badge}
        </span>
      ) : null}
    </a>
  )
}
