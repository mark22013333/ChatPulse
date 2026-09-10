import { useMemo } from 'react'
import { Loader2Icon } from 'lucide-react'
import { MasterDetailBack } from '@/app/MasterDetailBack'
import { Pane } from '@/app/Pane'
import { SmallScreenNotice } from '@/app/SmallScreenNotice'
import { StreamLiveRegions } from '@/app/StreamLiveRegions'
import { TopBar } from '@/app/TopBar'
import { useBootstrap } from '@/app/useBootstrap'
import { CommandPalette } from '@/components/CommandPalette'
import { SkipLink } from '@/components/common/SkipLink'
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

  /**
   * 768–1024 的主從切換（設計規格 §12）：清單與工作區同時只顯示一個。
   *
   * **判準直接來自網址，沒有另外一個狀態**——`#/summary` 是清單、
   * `#/summary/:key` 是工作區，收件匣同理。規格 §12 稱這是「選 URL 路由的
   * 額外報酬」，這裡就是在領那份報酬：少一個旗標就少一種不同步。
   *
   * ### 為什麼**不能**用 `max-lg:hidden` 藏起來
   *
   * 兩半都含虛擬清單（左欄的 436 筆 Space；草稿工作區的 Reference Space
   * 選擇器也是同一個 `SpaceList`），而 `display:none` 的容器量到的高度是 0
   * ——§7.2 早就寫了這件事。2026-09-10 實測：捲動位置 0 的清單進工作區再
   * 退回來，`scrollTop` 自己跳到 1296（第 12 列變成第一列）。所以這裡用的是
   * 與 `Pane` 同一套手法：**留著掛載**、移出版面流、`inert` 退出 tab 序與
   * 無障礙樹。這也是為什麼要用 `useBreakpoint()` 而不是純 CSS——`inert`
   * 是屬性不是樣式，non-active 的那一半必須在 JS 這一側知道。
   */
  const detail = route.section === 'mentions' ? route.mentionId !== null : route.spaceId !== null

  // 版面斷點：≥1280 證據欄常駐，以下改成抽屜（設計規格 §12）
  const breakpoint = useBreakpoint()
  const masterDetail = breakpoint === 'narrow' || breakpoint === 'tiny'
  const listHidden = masterDetail && detail
  const mainHidden = masterDetail && !detail
  const evidenceInline = breakpoint === 'wide'
  const drawerOpen = useUiStore((state) => state.drawer[breakpoint] === true)
  const toggleDrawer = useUiStore((state) => state.toggleDrawer)
  const closeDrawer = useUiStore((state) => state.closeDrawer)

  // 全域快捷鍵。送出類動作刻意沒有快捷鍵（設計規格 §11.1）
  useGlobalHotkeys({ onToggleEvidence: () => toggleDrawer(breakpoint) })

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
      <div className="flex h-full items-center justify-center gap-2 overflow-y-auto text-sm text-muted-foreground">
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
    <div className="flex h-full flex-col overflow-hidden bg-background text-foreground">
      {/* 「主要內容」會變：主從切換顯示清單那一半時 `<main>` 是 inert 的，
          那時清單本身就是主要內容 */}
      <SkipLink href={mainHidden ? '#rail' : '#main'} />

      <StreamLiveRegions />

      <TopBar />

      {/*
        三欄主體。兩個工作台都**常駐掛載**（設計規格 §7.2）。

        在此之前這裡是條件渲染，元件會真的卸載重掛——`SummaryWorkspace.tsx`
        與 `DraftReplyWorkspace.tsx` 的 reset 註解記錄了它造成的災情：掛載時的
        reset() 把還在串流的內容清光。現在改成保留掛載，那些條件式 reset 也就
        退化成第二道防線。
      */}
      {/* relative 是主從切換要的：收起來的那一半用 absolute inset-0 移出
          版面流，定位基準就是這個容器 */}
      <div className="relative flex min-h-0 flex-1">
        <aside
          id="rail"
          tabIndex={-1}
          aria-label={view === 'summary' ? 'Space 清單' : 'Mention 收件匣'}
          inert={listHidden}
          className={cn(
            // ≥1024 一律是固定寬度的左欄；以下走主從切換，所以寬度與右框線
            // 都掛在 lg: 上——清單獨占畫面時右邊沒有東西，那條線是多的
            'relative flex flex-col border-border outline-none lg:w-inbox lg:shrink-0 lg:border-r',
            listHidden
              ? 'pointer-events-none absolute inset-0 -z-10 w-full opacity-0'
              : 'max-lg:w-full max-lg:flex-1',
          )}
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

        <main
          id="main"
          tabIndex={-1}
          inert={mainHidden}
          className={cn(
            'relative flex min-w-0 flex-1 flex-col outline-none',
            // 主從切換：清單那一半顯示時工作區收起來。**不用 display:none**
            // ——草稿工作區的 Reference Space 選擇器也是虛擬清單（見上方說明）
            mainHidden && 'pointer-events-none absolute inset-0 -z-10 opacity-0',
          )}
        >
          {/* 只有主從切換的寬度需要一條回得去的路（元件自己 lg:hidden）。
              清單那一半顯示時整個 <main> 都收起來了，麵包屑也跟著不在。 */}
          {detail ? <MasterDetailBack view={view} /> : null}

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
