import { useMemo, type ReactNode } from 'react'
import { Loader2Icon } from 'lucide-react'
import { SmallScreenNotice } from '@/app/SmallScreenNotice'
import { TopBar } from '@/app/TopBar'
import { useBootstrap } from '@/app/useBootstrap'
import { useStreamAnnouncer } from '@/hooks/useStreamAnnouncer'
import { CommandPalette } from '@/components/CommandPalette'
import { EvidenceColumn } from '@/components/evidence/EvidenceColumn'
import { EvidenceDrawer } from '@/components/evidence/EvidenceDrawer'
import { DiagnosticsPage } from '@/components/settings/DiagnosticsPage'
import { SettingsOverlay } from '@/components/settings/SettingsOverlay'
import { DraftReplyWorkspace } from '@/components/draft/DraftReplyWorkspace'
import { LoginScreen } from '@/components/LoginScreen'
import { MentionInbox } from '@/components/MentionInbox'
import { ShortcutHelp } from '@/components/ShortcutHelp'
import { SpacesRail } from '@/components/SpacesRail'
import { SummaryHistory } from '@/components/SummaryHistory'
import { SummaryWorkspace } from '@/components/SummaryWorkspace'
import { hashForMentions } from '@/lib/route'
import { cn } from '@/lib/utils'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { useGlobalHotkeys } from '@/hooks/useGlobalHotkeys'
import { useRouter } from '@/router/useRouter'
import { useRouteSync } from '@/router/useRouteSync'
import { useMentionsStore } from '@/store/mentions'
import { findSpace, useSpacesStore } from '@/store/spaces'
import { useUiStore } from '@/store/ui'
import { useDraftStore } from '@/store/draft'

export function AppShell() {
  // 登入之後要把哪些資料準備好，全都在這裡（設計規格 §7.4）
  const { booting, authenticated } = useBootstrap()

  const { route, navigate } = useRouter()
  // URL 是「在看哪一個 Space／哪一則 Mention」的唯一真相（設計規格 §6.6）
  useRouteSync()
  const view = route.section === 'mentions' ? 'mentions' : 'summary'

  // 版面斷點：≥1280 證據欄常駐，以下改成抽屜（設計規格 §12）
  const breakpoint = useBreakpoint()
  const evidenceInline = breakpoint === 'wide'
  const drawerOpen = useUiStore((state) => state.drawer[breakpoint] === true)
  const toggleDrawer = useUiStore((state) => state.toggleDrawer)
  const closeDrawer = useUiStore((state) => state.closeDrawer)

  // 全域快捷鍵。送出類動作刻意沒有快捷鍵（設計規格 §11.1）
  useGlobalHotkeys({ onToggleEvidence: () => toggleDrawer(breakpoint) })

  // 串流的螢幕閱讀器宣告（設計規格 §10.6）。只訂閱布林值，不會被 chunk 帶著重繪
  const { polite: announcement, alert: alertAnnouncement } = useStreamAnnouncer()

  const spaces = useSpacesStore((state) => state.items)
  const selectedSpaceId = useSpacesStore((state) => state.selectedId)
  const selectedSpace = useMemo(() => findSpace(spaces, selectedSpaceId), [spaces, selectedSpaceId])

  const mentions = useMentionsStore((state) => state.items)
  const selectedMentionId = useMentionsStore((state) => state.selectedId)
  // 從摘要工作台建立的草稿目標不在收件匣清單裡（後端刻意過濾），優先用它
  const externalMention = useMentionsStore((state) => state.external)
  const selectedMention = useMemo(
    () =>
      externalMention ?? mentions.find((item) => item.id === selectedMentionId) ?? null,
    [externalMention, mentions, selectedMentionId],
  )

  if (booting) {
    return (
      <div className="flex min-h-dvh items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2Icon className="size-4 animate-spin" />
        正在確認登入狀態…
      </div>
    )
  }

  // 診斷頁刻意排在登入 gate **之前**：「後端起來了嗎、AI 供應商設好了嗎」
  // 正是還沒登入時最需要問的事（設計規格 §6.5）
  if (route.section === 'settings' && route.settingsTab === 'diagnostics') {
    return <DiagnosticsPage />
  }

  if (!authenticated) return <LoginScreen />

  return (
    // <768 先給一個誠實的說明頁（含逃生門）。Draft Reply 送出不可撤回，
    // 而它的證據在手機寬度下讀不了——讀不了就等於在不知情的狀況下送出。
    <SmallScreenNotice>
    <div className="flex h-dvh flex-col overflow-hidden bg-background text-foreground">
      {/* 左欄的虛擬清單有 436 筆，鍵盤使用者要 Tab 很久才到得了主要內容 */}
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:bg-raised focus:text-foreground focus:shadow-overlay focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:px-3 focus:py-2 focus:text-sm"
      >
        跳到主要內容
      </a>

      {/*
        串流的狀態層宣告（設計規格 §10.6）。

        **這兩個容器一定要常駐**：live region 必須在內容寫進去**之前**就存在
        於無障礙樹裡，否則多數螢幕閱讀器不會念——「有訊息才渲染」是這個
        機制最常見的壞法。所以這裡永遠掛著，只是內容多半是空字串。

        內容層（`Markdown`）刻意**沒有** aria-live，只有 aria-busy：
        每個 chunk 都重寫 innerHTML，設了 aria-live 等於整段重念。
      */}
      <div role="status" aria-live="polite" className="sr-only">
        {announcement}
      </div>
      {/* 錯誤走 role="alert"（隱含 assertive，會打斷）——只有它值得打斷 */}
      <div role="alert" className="sr-only">
        {alertAnnouncement}
      </div>

      <TopBar />

      {/*
        三欄主體。兩個工作台都**常駐掛載**（設計規格 §7.2）。

        在此之前這裡是條件渲染，元件會真的卸載重掛——`SummaryWorkspace.tsx`
        與 `DraftReplyWorkspace.tsx` 的 reset 註解記錄了它造成的災情：掛載時的
        reset() 把還在串流的內容清光。現在改成保留掛載，那些條件式 reset 也就
        退化成第二道防線。
      */}
      <div className="flex min-h-0 flex-1">
        <aside
          aria-label={view === 'summary' ? 'Space 清單' : 'Mention 收件匣'}
          className="relative flex w-inbox shrink-0 flex-col border-r border-border"
        >
          <Pane active={view === 'summary'}>
            <SpacesRail />
          </Pane>
          <Pane active={view === 'mentions'}>
            <MentionInbox
              onSelect={(id) => navigate(hashForMentions(id))}
              onMergedGenerate={(primaryId, mergeIds) => {
                // 一定要把 mergeIds 帶進網址。少了它，useRouteSync 反向同步
                // 時會判定「網址上沒有合併」而清空勾選，DraftReplyWorkspace
                // 的 activeMergeIds 跟著變空——按下去的那一刻合併就散了。
                navigate(hashForMentions(primaryId, mergeIds))
                void useDraftStore.getState().generate(primaryId, mergeIds)
              }}
            />
          </Pane>
        </aside>

        <main id="main" tabIndex={-1} className="relative flex min-w-0 flex-1 flex-col outline-none">
          <Pane active={view === 'summary'}>
            <SummaryWorkspace
              space={selectedSpace}
              active={view === 'summary'}
              // selectExternal 已經把 selectedId 設好了，useRouteSync 的去重
              // 會跳過 select()，external 才不會被清掉（見 useRouteSync 註解）
              onDraftCreated={(mentionId) => navigate(hashForMentions(mentionId))}
            />
          </Pane>
          <Pane active={view === 'mentions'}>
            <DraftReplyWorkspace mention={selectedMention} active={view === 'mentions'} />
          </Pane>
        </main>

        {/*
          右欄＝證據欄（設計規格 §5）。

          它以前是個雜物抽屜：採集器狀態、Token 用量、參考專案設定、歷史
          Summary 全塞在這裡，而且 1024px 以下整欄消失。現在它只回答一個
          問題——「這份產出建立在什麼之上」。設定類的東西進了設定中心，
          採集器狀態進了診斷頁，用量進了設定的資料頁。
        */}
        {/* ≥1280 常駐；以下改成抽屜（設計規格 §12）。在此之前這裡是
            `hidden lg:flex`，1024px 以下整欄消失且沒有替代入口——那正是
            這次改版要修的功能性破洞之一。 */}
        <aside
          aria-label="證據"
          className="relative hidden w-rail shrink-0 flex-col border-l border-border xl:flex"
        >
          <Pane active={view === 'summary'}>
            <EvidenceColumn origin="summary" />
            {/* 歷史 Summary 留在這裡：它是「我在這個工作台做過什麼」，
                與證據同一個情境，不是設定。 */}
            <SummaryHistory />
          </Pane>
          <Pane active={view === 'mentions'}>
            <EvidenceColumn origin="draft" />
          </Pane>
        </aside>
      </div>

      <CommandPalette />

      {/* `?` 的快捷鍵說明。放在設定覆蓋層**之前**沒有影響——它自己是
          fixed z-50，而 Esc 的層次由 store 的 isOverlayOpen() 決定，
          不靠 DOM 順序 */}
      <ShortcutHelp />

      <EvidenceDrawer
        open={!evidenceInline && drawerOpen}
        origin={view === 'mentions' ? 'draft' : 'summary'}
        onClose={() => closeDrawer(breakpoint)}
      />

      {/* 設定中心是覆蓋層：工作台仍掛在後面，串流不中斷、狀態不掉 */}
      {route.section === 'settings' ? <SettingsOverlay /> : null}
    </div>
    </SmallScreenNotice>
  )
}

/**
 * 常駐掛載的其中一半（設計規格 §7.2）。
 *
 * **不用 `display:none`**：`SpaceList` 用 `@tanstack/react-virtual`，在
 * `display:none` 的容器裡量到的高度是 0，切回來會重新 measure——畫面閃一下、
 * 捲動位置歸零。改成保留尺寸的絕對定位，並用 React 19 原生的 `inert` 讓看不見
 * 的那一半退出 tab 序與無障礙樹（只靠 `aria-hidden` 擋不住 Tab）。
 */
function Pane({ active, children }: { active: boolean; children: ReactNode }) {
  return (
    <div
      className={cn(
        'flex min-h-0 flex-col',
        active ? 'flex-1' : 'pointer-events-none absolute inset-0 -z-10 opacity-0',
      )}
      inert={!active}
    >
      {children}
    </div>
  )
}
