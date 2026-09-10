import { useEffect, useMemo, type ReactNode } from 'react'
import {
  InboxIcon,
  Loader2Icon,
  LogOutIcon,
  PanelRightIcon,
  SearchIcon,
  SettingsIcon,
  SparklesIcon,
} from 'lucide-react'
import { PulseMark } from '@/app/PulseMark'
import { SmallScreenNotice } from '@/app/SmallScreenNotice'
import { CommandPalette } from '@/components/CommandPalette'
import { Button } from '@/components/ui/button'
import { EvidenceColumn, useEvidenceBundle } from '@/components/evidence/EvidenceColumn'
import { EvidenceDrawer } from '@/components/evidence/EvidenceDrawer'
import { DiagnosticsPage } from '@/components/settings/DiagnosticsPage'
import { SettingsOverlay } from '@/components/settings/SettingsOverlay'
import { DraftReplyWorkspace } from '@/components/DraftReplyWorkspace'
import { LoginScreen } from '@/components/LoginScreen'
import { MentionInbox } from '@/components/MentionInbox'
import { SpacesRail } from '@/components/SpacesRail'
import { SummaryHistory } from '@/components/SummaryHistory'
import { SummaryWorkspace } from '@/components/SummaryWorkspace'
import { ThemeToggle } from '@/components/ThemeToggle'
import { hashForMentions, hashForSettings, hashForSummary } from '@/lib/route'
import { cn } from '@/lib/utils'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { useGlobalHotkeys } from '@/hooks/useGlobalHotkeys'
import { useRouter } from '@/router/useRouter'
import { useRouteSync } from '@/router/useRouteSync'
import { useAuthStore } from '@/store/auth'
import { useMentionsStore } from '@/store/mentions'
import { useProviderStore } from '@/store/providers'
import { useReplySettingsStore } from '@/store/replySettings'
import { findSpace, useSpacesStore } from '@/store/spaces'
import { useSummaryStore } from '@/store/summary'
import { useUiStore } from '@/store/ui'
import { useDraftStore } from '@/store/draft'

export default function App() {
  const booting = useAuthStore((state) => state.booting)
  const status = useAuthStore((state) => state.status)
  const me = useAuthStore((state) => state.me)
  const init = useAuthStore((state) => state.init)
  const logout = useAuthStore((state) => state.logout)

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
  // 抽屜關著時，頂列的證據鈕要讓人知道「值得打開看一眼」
  const { bundle: evidence } = useEvidenceBundle(view === 'mentions' ? 'draft' : 'summary')
  const attentionCount = evidence.attentionCount

  // 全域快捷鍵。送出類動作刻意沒有快捷鍵（設計規格 §11.1）
  useGlobalHotkeys({ onToggleEvidence: () => toggleDrawer(breakpoint) })
  const setPaletteOpen = useUiStore((state) => state.setPaletteOpen)

  // 讓頁籤能顯示「另一邊還在生成」。訂閱的是布林值，只有開始／結束時才變，
  // 不會每個 chunk 都讓整個 App 重繪。
  const summaryStreaming = useSummaryStore((state) => state.streaming)
  const draftStreaming = useDraftStore((state) => state.streaming)

  const spaces = useSpacesStore((state) => state.items)
  const selectedSpaceId = useSpacesStore((state) => state.selectedId)
  const selectedSpace = useMemo(() => findSpace(spaces, selectedSpaceId), [spaces, selectedSpaceId])

  const mentions = useMentionsStore((state) => state.items)
  const selectedMentionId = useMentionsStore((state) => state.selectedId)
  const pendingCount = useMentionsStore((state) => state.counts.pending)
  const seedCounts = useMentionsStore((state) => state.seedCounts)
  // 從摘要工作台建立的草稿目標不在收件匣清單裡（後端刻意過濾），優先用它
  const externalMention = useMentionsStore((state) => state.external)
  const selectedMention = useMemo(
    () =>
      externalMention ?? mentions.find((item) => item.id === selectedMentionId) ?? null,
    [externalMention, mentions, selectedMentionId],
  )

  const loadStyles = useSummaryStore((state) => state.loadStyles)
  const loadHistory = useSummaryStore((state) => state.loadHistory)
  const applyDefaults = useSummaryStore((state) => state.applyDefaults)

  const applyProviderConfig = useProviderStore((state) => state.applyServerConfig)
  const loadProviders = useProviderStore((state) => state.loadProviders)
  const providersLoaded = useProviderStore((state) => state.initialised)

  const applyReplyPreferences = useReplySettingsStore((state) => state.applyPreferences)

  useEffect(() => {
    void init()
  }, [init])

  const authenticated = status?.authenticated === true

  useEffect(() => {
    if (!authenticated) return
    void loadStyles()
    void loadHistory()
  }, [authenticated, loadStyles, loadHistory])

  /*
   * 資料層的 bootstrap（設計規格 §7.4）。
   *
   * Space 清單與 Mention 輪詢本來各自住在 SpacesRail 與 MentionInbox 的
   * useEffect 裡，於是「什麼時候載入」由「哪個畫面剛好先掛載」決定：人在摘要
   * 工作台時頂列的未處理數字不會動，而切一次頁籤就重新開始計時。它們屬於資料，
   * 不屬於畫面。
   */
  useEffect(() => {
    if (!authenticated) return
    const spaces = useSpacesStore.getState()
    if (spaces.items.length === 0) void spaces.load()

    const mentions = useMentionsStore.getState()
    mentions.startPolling()
    return () => mentions.stopPolling()
  }, [authenticated])

  // 以 /api/v1/me 的偏好當作抓取則數與風格的初始值
  useEffect(() => {
    if (!me?.preferences) return
    applyDefaults({
      limit: me.preferences.default_limit,
      style: me.preferences.default_style,
    })
  }, [me?.preferences, applyDefaults])

  // 回覆設定的偏好（ADR-0007）：口氣／Persona／提示詞／潤稿。
  // 技術上不套也能運作（送出時省略欄位，後端自己會讀偏好），但那樣側欄的
  // 下拉會顯示「跟隨預設」而實際上有生效——畫面與行為不一致比沒有預設更糟。
  // store 內建 initialised 旗標，不會覆寫使用者當下已經改過的選擇。
  useEffect(() => {
    if (!me?.preferences) return
    applyReplyPreferences(me.preferences)
  }, [me?.preferences, applyReplyPreferences])

  // 供應商清單：/me 已經帶了 ai 就直接用（少一次往返），否則補打 /providers。
  // 初始選擇＝偏好的 default_provider → 沒有就用伺服器的 default（見 store/providers.ts）
  useEffect(() => {
    if (!authenticated) return
    if (me?.ai?.providers?.length) {
      applyProviderConfig(me.ai, me.preferences?.default_provider ?? null)
    } else if (!providersLoaded) {
      void loadProviders()
    }
  }, [
    authenticated,
    me?.ai,
    me?.preferences?.default_provider,
    providersLoaded,
    applyProviderConfig,
    loadProviders,
  ])

  // 未處理數量先用 /api/v1/me 的快照，頂列 badge 不必等收件匣開啟才出現
  useEffect(() => {
    if (!me?.mention_counts) return
    seedCounts(me.mention_counts)
  }, [me?.mention_counts, seedCounts])

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

      {/* 頂列 */}
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
          {/* 說明改成頁籤可及名稱的一部分，不放進 title——title 對鍵盤與
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
