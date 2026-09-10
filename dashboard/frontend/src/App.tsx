import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { InboxIcon, Loader2Icon, LogOutIcon, SparklesIcon } from 'lucide-react'
import { PulseMark } from '@/app/PulseMark'
import { Button } from '@/components/ui/button'
import { CodeProjectSettings } from '@/components/CodeProjectSettings'
import { CollectorPanel } from '@/components/CollectorPanel'
import { DraftReplyWorkspace } from '@/components/DraftReplyWorkspace'
import { LoginScreen } from '@/components/LoginScreen'
import { MentionInbox } from '@/components/MentionInbox'
import { SpacesRail } from '@/components/SpacesRail'
import { SummaryHistory } from '@/components/SummaryHistory'
import { SummaryWorkspace } from '@/components/SummaryWorkspace'
import { ThemeToggle } from '@/components/ThemeToggle'
import { UsagePanel } from '@/components/UsagePanel'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/store/auth'
import { useMentionsStore } from '@/store/mentions'
import { useProviderStore } from '@/store/providers'
import { useReplySettingsStore } from '@/store/replySettings'
import { findSpace, useSpacesStore } from '@/store/spaces'
import { useSummaryStore } from '@/store/summary'
import { useDraftStore } from '@/store/draft'

type View = 'summary' | 'mentions'

export default function App() {
  const booting = useAuthStore((state) => state.booting)
  const status = useAuthStore((state) => state.status)
  const me = useAuthStore((state) => state.me)
  const init = useAuthStore((state) => state.init)
  const logout = useAuthStore((state) => state.logout)

  const [view, setView] = useState<View>('summary')

  // 讓頁籤能顯示「另一邊還在生成」。訂閱的是布林值，只有開始／結束時才變，
  // 不會每個 chunk 都讓整個 App 重繪。
  const summaryStreaming = useSummaryStore((state) => state.streaming)
  const draftStreaming = useDraftStore((state) => state.streaming)

  const spaces = useSpacesStore((state) => state.items)
  const selectedSpaceId = useSpacesStore((state) => state.selectedId)
  const selectedSpace = useMemo(() => findSpace(spaces, selectedSpaceId), [spaces, selectedSpaceId])

  const mentions = useMentionsStore((state) => state.items)
  const selectedMentionId = useMentionsStore((state) => state.selectedId)
  const selectMention = useMentionsStore((state) => state.select)
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

  if (!authenticated) return <LoginScreen />

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-background text-foreground">
      {/* 頂列 */}
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-border px-4">
        <div className="flex items-center gap-2">
          <span className="flex size-6 items-center justify-center rounded-md bg-sky-500/15 text-sky-500 ring-1 ring-sky-500/25">
            <PulseMark className="size-3.5" />
          </span>
          <span className="text-sm font-semibold tracking-tight">ChatPulse</span>
        </div>

        <nav className="ml-2 flex items-center gap-1 rounded-lg bg-muted p-0.5">
          <ViewTab
            active={view === 'summary'}
            onClick={() => setView('summary')}
            icon={<SparklesIcon className="size-3.5" />}
            label="摘要工作台"
            busy={summaryStreaming}
          />
          <ViewTab
            active={view === 'mentions'}
            onClick={() => setView('mentions')}
            icon={<InboxIcon className="size-3.5" />}
            label="Mention 收件匣"
            badge={pendingCount}
            busy={draftStreaming}
          />
        </nav>

        <div className="ml-auto flex items-center gap-2">
          {me?.viewer ? (
            <span className="hidden text-[11px] text-muted-foreground sm:inline">
              {me.viewer.display_name || me.viewer.email}
            </span>
          ) : null}
          <ThemeToggle />
          <Button size="sm" variant="ghost" onClick={() => void logout()}>
            <LogOutIcon />
            登出
          </Button>
        </div>
      </header>

      {/* 三欄主體 */}
      <div className="flex min-h-0 flex-1">
        <aside className="flex w-72 shrink-0 flex-col border-r border-border">
          {view === 'summary' ? (
            <SpacesRail />
          ) : (
            <MentionInbox
              onSelect={(id) => selectMention(id)}
              onMergedGenerate={(primaryId, mergeIds) => {
                selectMention(primaryId)
                void useDraftStore.getState().generate(primaryId, mergeIds)
              }}
            />
          )}
        </aside>

        <main className="flex min-w-0 flex-1 flex-col">
          {view === 'summary' ? (
            <SummaryWorkspace
              space={selectedSpace}
              onDraftCreated={() => setView('mentions')}
            />
          ) : (
            <DraftReplyWorkspace mention={selectedMention} />
          )}
        </main>

        <aside className="hidden w-72 shrink-0 flex-col border-l border-border lg:flex">
          {view === 'summary' ? (
            <>
              <SummaryHistory />
              <UsagePanel />
            </>
          ) : (
            <>
              <CollectorPanel />
              <div className="min-h-0 flex-1 overflow-y-auto">
                {/* 參考專案設定放這裡：與 Draft Reply 同一個情境，
                    調整分支對應之後馬上就能在左邊勾選使用。 */}
                <div className="border-b border-border p-3">
                  <CodeProjectSettings />
                </div>
                <SummaryHistory />
              </div>
              <UsagePanel />
            </>
          )}
        </aside>
      </div>
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
        <span
          className="relative flex size-1.5"
          title="正在生成，切到別的頁籤也會繼續"
          aria-label="正在生成"
        >
          {/* motion-safe：尊重使用者的「減少動態效果」系統設定 */}
          <span className="absolute inline-flex size-full rounded-full bg-sky-500 opacity-75 motion-safe:animate-ping" />
          <span className="relative inline-flex size-1.5 rounded-full bg-sky-500" />
        </span>
      ) : null}
      {badge && badge > 0 ? (
        <span className="inline-flex min-w-4 items-center justify-center rounded-full bg-sky-500 px-1 text-[10px] font-semibold text-white">
          {badge}
        </span>
      ) : null}
    </button>
  )
}
