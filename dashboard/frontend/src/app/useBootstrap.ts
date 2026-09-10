import { useEffect } from 'react'
import { useAuthStore } from '@/store/auth'
import { useMentionsStore } from '@/store/mentions'
import { useProviderStore } from '@/store/providers'
import { useReplySettingsStore } from '@/store/replySettings'
import { useSpacesStore } from '@/store/spaces'
import { useSummaryStore } from '@/store/summary'

/**
 * 資料層的 bootstrap（設計規格 §7.4）。
 *
 * Space 清單與 Mention 輪詢本來各自住在 `SpacesRail` 與 `MentionInbox` 的
 * useEffect 裡，於是「什麼時候載入」由「哪個畫面剛好先掛載」決定：人在摘要
 * 工作台時頂列的未處理數字不會動，而切一次頁籤就重新開始計時。**它們屬於
 * 資料，不屬於畫面**——所以搬到這裡，由 AppShell 在登入後啟動一次。
 *
 * 抽成 hook 而不是留在 AppShell 裡，是因為這七個 effect 是一個完整的單位
 * （「登入之後要把哪些東西準備好」），而 AppShell 剩下的職責是版面與 gate。
 *
 * 回傳 `authenticated`：那是所有 effect 的共同前置條件，AppShell 也要用它
 * 決定畫登入頁還是主畫面，算一次就好。
 */
export function useBootstrap(): { booting: boolean; authenticated: boolean } {
  const booting = useAuthStore((s) => s.booting)
  const status = useAuthStore((s) => s.status)
  const me = useAuthStore((s) => s.me)
  const init = useAuthStore((s) => s.init)

  const loadStyles = useSummaryStore((s) => s.loadStyles)
  const loadHistory = useSummaryStore((s) => s.loadHistory)
  const applyDefaults = useSummaryStore((s) => s.applyDefaults)

  const applyProviderConfig = useProviderStore((s) => s.applyServerConfig)
  const loadProviders = useProviderStore((s) => s.loadProviders)
  const providersLoaded = useProviderStore((s) => s.initialised)

  const applyReplyPreferences = useReplySettingsStore((s) => s.applyPreferences)
  const seedCounts = useMentionsStore((s) => s.seedCounts)

  useEffect(() => {
    void init()
  }, [init])

  const authenticated = status?.authenticated === true

  useEffect(() => {
    if (!authenticated) return
    void loadStyles()
    void loadHistory()
  }, [authenticated, loadStyles, loadHistory])

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

  return { booting, authenticated }
}
