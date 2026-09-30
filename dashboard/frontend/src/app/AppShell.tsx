import { useMemo } from 'react'
import { Loader2Icon } from 'lucide-react'
import { MasterDetailBack } from '@/app/MasterDetailBack'
import { Pane } from '@/app/Pane'
import { SideNav } from '@/app/SideNav'
import { SmallScreenNotice } from '@/app/SmallScreenNotice'
import { StreamLiveRegions } from '@/app/StreamLiveRegions'
import { TopBar } from '@/app/TopBar'
import { useBootstrap } from '@/app/useBootstrap'
import { workbenchLayout } from '@/app/workbenchLayout'
import { CommandPalette } from '@/components/CommandPalette'
import { SkipLink } from '@/components/common/SkipLink'
import { EvidenceColumn } from '@/components/evidence/EvidenceColumn'
import { EvidenceDrawer } from '@/components/evidence/EvidenceDrawer'
import { DiagnosticsPage } from '@/components/settings/DiagnosticsPage'
import { TokenSheet } from '@/dev/TokenSheet'
import { SettingsOverlay } from '@/components/settings/SettingsOverlay'
import { DraftReplyWorkspace } from '@/components/draft/DraftReplyWorkspace'
import { LoginScreen } from '@/components/LoginScreen'
import { MentionInbox } from '@/components/MentionInbox'
import { ShortcutHelp } from '@/components/ShortcutHelp'
import { SpacesTable } from '@/components/SpacesTable'
import { SummaryHistory } from '@/components/SummaryHistory'
import { SummaryWorkspace } from '@/components/SummaryWorkspace'
import { WorklogDayWorkspace } from '@/components/worklog/WorklogDayWorkspace'
import { WorklogTable } from '@/components/worklog/WorklogTable'
import { evidenceOriginOf } from '@/lib/evidence'
import { toWorkbench, type WorkbenchView } from '@/lib/modules'
import { hasDetail, hashForMentions } from '@/lib/route'
import { cn } from '@/lib/utils'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { useGlobalHotkeys } from '@/hooks/useGlobalHotkeys'
import { useRouter } from '@/router/useRouter'
import { useRouteSync } from '@/router/useRouteSync'
import { useMentionsStore } from '@/store/mentions'
import { findSpace, useSpacesStore } from '@/store/spaces'
import { useUiStore } from '@/store/ui'
import { useDraftStore } from '@/store/draft'

/**
 * 左欄清單的無障礙名稱——每個工作台的清單裝的是不同東西。
 *
 * 查表而不是三元式：漏掉一個工作台時，螢幕閱讀器會把「本週」念成
 * 「Mention 收件匣」，而這種錯誤在視覺上完全看不出來。
 */
const RAIL_LABEL: Record<WorkbenchView, string> = {
  summary: 'Space 清單',
  mentions: 'Mention 收件匣',
  worklog: '工時草稿清單',
}

export function AppShell() {
  // 登入之後要把哪些資料準備好，全都在這裡（設計規格 §7.4）
  const { booting, authenticated } = useBootstrap()

  const { route, navigate } = useRouter()
  // URL 是「在看哪一個 Space／哪一則 Mention／哪一天」的唯一真相（設計規格 §6.6）
  useRouteSync()
  // 三個工作台。**不要改回 `section === 'x' ? A : B`**——那種寫法在加第四個
  // 工作台時不會編譯失敗，只會安靜地把它當成摘要（見 `toWorkbench` 的註解）
  const view = toWorkbench(route.section)

  /**
   * 主從切換：清單與工作區同時只顯示一個。對話型工作台只在 768–1024
   * （設計規格 §12）；表格型（摘要工作台，2026-10-01 改版 §4.1）在**所有寬度**
   * ——表格獨占主體、點一列整頁切到詳情。哪些是表格型見 `app/workbenchLayout.ts`。
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
  const detail = hasDetail(route)

  // 版面斷點：≥1280 證據欄常駐，以下改成抽屜（設計規格 §12）
  const breakpoint = useBreakpoint()
  const { listHidden, mainHidden, listFullWidth, evidenceHidden } = workbenchLayout(
    view,
    detail,
    breakpoint,
  )
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

  // 開發用的 token 對照表（規格 §16.3）。排在所有 gate 之前——它不需要任何資料，
  // 而換配色時常常就是在還沒登入的狀態下要看。
  //
  // 判準讀 `window.location.hash` 而不是 `route.section`：`#/dev/tokens` 不是
  // 產品模組，不該進模組表、也不該混進 `Section` 型別。parseHash 會把它
  // fallback 成 summary，但 hash 字串本身還在，拿它判就好。
  //
  // `import.meta.env.DEV` 在正式建置會被替換成 `false`，整段變成死碼，
  // TokenSheet 連同它的 import 一起被 tree-shake 掉（建置後 grep 得出來）。
  if (import.meta.env.DEV && window.location.hash.startsWith('#/dev/tokens')) {
    return <TokenSheet />
  }

  if (booting) {
    return (
      // 單行內容，永遠不會溢出，所以不掛 overflow——掛了反而會與 `items-center`
      // 湊成「上緣捲不到」那個地雷（見 LoginScreen 的註解）
      <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
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
    {/*
      **`overflow-clip` 不是 `overflow-hidden`。** hidden 仍然建立捲動容器，
      只是把捲軸藏起來——程式化捲動與「瀏覽器把焦點元素捲進畫面」照樣有效，
      於是整個 app（含頂列）會被捲上去，而使用者連捲回來的捲軸都沒有。

      2026-09-11 實測（這就是使用者回報的「畫面整個往上移動」）：點參考專案的
      環境 chip 會把焦點交給 `<label>` 裡那個 `sr-only` 的 checkbox
      （`CodeRefPicker.tsx`），瀏覽器為了讓焦點元素可見就捲了這一層
      ——`scrollTop` 變成 457、頂列跑到 −457。改成 `clip` 之後這一層根本不是
      捲動容器，焦點捲動會落到它該落的地方（設定欄自己的捲動區）。

      **註解一定要用 `{...}` 包**：這裡是 JSX 的 children 位置，`//` 不是註解
      而是**文字節點**。2026-09-11 就這樣把整段中文渲染到畫面上、把 shell
      往下推了 45px（`shellTop` 從 0 變 45），是自己的探針量出來才發現的。
    */}
    <div className="flex h-full overflow-clip bg-background text-foreground">
      {/* 「主要內容」會變：主從切換顯示清單那一半時 `<main>` 是 inert 的，
          那時清單本身就是主要內容 */}
      <SkipLink href={mainHidden ? '#rail' : '#main'} />

      <StreamLiveRegions />

      {/*
        左側主導覽。**外框從 flex-col 改成 flex-row**：導覽是版面的第一欄，
        頂列與三欄工作區都住在它右邊的直向容器裡。

        這個順序有意義——頂列不橫跨導覽，導覽也不被頂列切成上下兩半。
        兩者都是「全站級」的東西，但一個回答「有哪些模組」、一個回答
        「這個模組裡你在哪」，讓它們正交比讓它們交叉好讀。

        SkipLink 與 StreamLiveRegions 都是 sr-only（焦點時才 absolute 浮出），
        放在 flex-row 容器裡不會佔掉一欄寬度。
      */}
      <SideNav />

      <div className="flex min-w-0 flex-1 flex-col">
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
          aria-label={RAIL_LABEL[view]}
          inert={listHidden}
          className={cn(
            'relative flex flex-col border-border outline-none',
            // 對話型：≥1024 是固定寬度的左欄，以下走主從切換，所以寬度與右框線
            // 都掛在 lg: 上——清單獨占畫面時右邊沒有東西，那條線是多的。
            // 表格型：所有寬度都是清單獨占，根本沒有「左欄」
            listFullWidth ? 'w-full' : 'lg:w-inbox lg:shrink-0 lg:border-r max-lg:w-full max-lg:flex-1',
            listHidden
              ? 'pointer-events-none absolute inset-0 -z-10 opacity-0'
              : listFullWidth && 'flex-1',
          )}
        >
          <Pane active={view === 'summary'}>
            {/* 整頁表格（2026-10-01 改版 §4）。收起來時**不卸載**：tabs、篩選、
                頁碼是它的元件 state，返回時要原樣還在（AppShell.test.tsx 守著） */}
            <SpacesTable />
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
          <Pane active={view === 'worklog'}>
            {/* 整頁表格（2026-10-01 改版 §4.4），取代舊的週欄。傳 active：Pane
                不卸載子元件，沒有這道閘的話光是打開 app 就會對 ZPlanner 發一次
                請求（AppShell.test.tsx 守著）。收起來時同樣不卸載 */}
            <WorklogTable active={view === 'worklog'} />
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
          {/* 主從切換時需要一條回得去的路：表格型在所有寬度出現，對話型只在
              768–1024（元件自己判斷 lg:hidden）。清單那一半顯示時整個 <main>
              都收起來了，返回列也跟著不在。 */}
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
          <Pane active={view === 'worklog'}>
            <WorklogDayWorkspace date={route.worklogDate} />
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
          // 表格獨占主體時證據欄收起來：它回答的是「這份產出建立在什麼之上」，
          // 旁邊沒有任何一份產出時掛著它只是噪音。手法同左右兩半——留著掛載、
          // inert ＋ 透明，**不用 display:none**（歷史 Summary 的捲動位置要留著）
          inert={evidenceHidden}
          className={cn(
            'relative hidden w-rail shrink-0 flex-col border-l border-border xl:flex',
            evidenceHidden && 'pointer-events-none absolute inset-y-0 right-0 -z-10 opacity-0',
          )}
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
          <Pane active={view === 'worklog'}>
            <EvidenceColumn origin="worklog" />
          </Pane>
        </aside>
        </div>
      </div>

      <CommandPalette />

      {/* `?` 的快捷鍵說明。放在設定覆蓋層**之前**沒有影響——它自己是
          fixed z-50，而 Esc 的層次由 store 的 isOverlayOpen() 決定，
          不靠 DOM 順序 */}
      <ShortcutHelp />

      <EvidenceDrawer
        open={!evidenceInline && drawerOpen}
        origin={evidenceOriginOf(route.section)}
        onClose={() => closeDrawer(breakpoint)}
      />

      {/* 設定中心是覆蓋層：工作台仍掛在後面，串流不中斷、狀態不掉 */}
      {route.section === 'settings' ? <SettingsOverlay /> : null}
    </div>
    </SmallScreenNotice>
  )
}
