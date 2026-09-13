import { useEffect, useRef } from 'react'
import { toast } from 'sonner'
import {
  isTypingTarget,
  resolveHotkey,
  SEQUENCE_PREFIX,
  SEQUENCE_TIMEOUT_MS,
  type HotkeyAction,
} from '@/lib/hotkeys'
import { isComposing } from '@/lib/keyboard'
import { copyText } from '@/lib/clipboard'
import { stepId } from '@/lib/listNavigation'
import { hashForMentions, hashForSettings, hashForSummary } from '@/lib/route'
import { useRouter } from '@/router/useRouter'
import { useDraftStore } from '@/store/draft'
import { selectMentionsByState, useMentionsStore } from '@/store/mentions'
import { useSpacesStore } from '@/store/spaces'
import { useSummaryStore } from '@/store/summary'
import { useUiStore } from '@/store/ui'

interface Handlers {
  onToggleEvidence: () => void
}

/** 兩個工作台之中，現在哪一個是前景。與 `AppShell` 的 `view` 同一個判準。 */
type Target = 'summary' | 'draft'

/**
 * 全域快捷鍵（設計規格 §11.1 那張表）。
 *
 * 四條規則：
 * 1. **輸入法組字中一律不理**——注音選字按 Enter 是「選這個字」
 * 2. 命令面板或說明面板開著時整組停用，交給那一層自己處理
 * 3. 單鍵快捷鍵在輸入框裡不生效（帶 mod 的組合鍵與 `Esc` 例外）
 * 4. 動作一律走 `navigate` 與 store 的公開 action，不直接改 store 內部狀態
 *
 * ### 為什麼這個 hook 自己去讀 store，而不是由 AppShell 傳一堆 handler 進來
 *
 * 快捷鍵要用到的東西橫跨五個 store（摘要、草稿、Mention、Space、UI）。全部
 * 用 selector 訂閱的話，這個 hook 會在每次串流 chunk、每次 Mention 輪詢後
 * 重繪 `AppShell` ——正是規格 §9.3 要修掉的問題。所以除了證據欄開關（那個
 * 值本來就在 AppShell 手上）之外，其餘一律在**按鍵發生的那一刻**才
 * `getState()` 讀一次。
 *
 * ### 刻意沒有的鍵
 *
 * **送出回話與推播回 Google Chat 沒有快捷鍵**（§11.1）：不可撤回的動作不該
 * 有肌肉記憶。⌘Enter 與 ⌘. 只碰「開始／停止生成」，兩者都可以重來。
 */
export function useGlobalHotkeys({ onToggleEvidence }: Handlers) {
  const paletteOpen = useUiStore((state) => state.paletteOpen)
  const helpOpen = useUiStore((state) => state.helpOpen)
  const setPaletteOpen = useUiStore((state) => state.setPaletteOpen)
  const setHelpOpen = useUiStore((state) => state.setHelpOpen)
  const { route, navigate } = useRouter()

  // 兩鍵序列（`g s`／`g m`／`g ,`／`g h`）的待處理前綴與它的逾時計時器。
  // 用 ref 而不是 state：前綴不影響畫面，設成 state 會讓按一下 `g` 就重繪。
  const pending = useRef<string | null>(null)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    const clearPending = () => {
      pending.current = null
      if (timer.current !== null) {
        window.clearTimeout(timer.current)
        timer.current = null
      }
    }

    /** 現在哪一個工作台是前景。設定中心是覆蓋層，後面仍然是摘要那一半。 */
    const targetOf = (): Target => (route.section === 'mentions' ? 'draft' : 'summary')

    const startGenerate = () => {
      // 設定開著時不准開始生成：使用者的注意力在設定上，而這個動作會燒配額。
      // 停止（⌘.）沒有這條限制——那是安全閥，任何時候都該能按。
      if (route.section === 'settings') return

      if (targetOf() === 'draft') {
        const draft = useDraftStore.getState()
        if (draft.streaming) return
        const { selectedId, external, mergeIds } = useMentionsStore.getState()
        const mentionId = external?.id ?? selectedId
        if (mentionId === null) {
          toast.info('先在收件匣挑一則 Mention，才能產生 Draft Reply')
          return
        }
        // 與 GenerateButton 同一條規則：只有「這一則自己也在勾選裡」才算合併，
        // 否則勾了 A、B 卻打開 C 按產生，會把不相干的兩則一起回掉
        void draft.generate(mentionId, mergeIds.includes(mentionId) ? mergeIds : [])
        return
      }

      const summary = useSummaryStore.getState()
      if (summary.streaming) return
      const spaceId = useSpacesStore.getState().selectedId
      if (!spaceId) {
        toast.info('先在左欄挑一個 Space，才能開始摘要')
        return
      }
      void summary.start(spaceId)
    }

    const stopStream = () => {
      // 兩邊都可能在跑（切頁籤不中止生成，見 §7.2），所以兩邊都停。
      // 只停前景那一半的話，使用者按了 ⌘. 卻還在燒配額——而畫面上看不出來。
      let stopped = false
      if (useSummaryStore.getState().streaming) {
        useSummaryStore.getState().abort()
        stopped = true
      }
      if (useDraftStore.getState().streaming) {
        useDraftStore.getState().abort()
        stopped = true
      }
      return stopped
    }

    const copyMarkdown = async () => {
      if (targetOf() === 'draft') {
        const { replyText, raw } = useDraftStore.getState()
        // 優先給**建議回話**：那才是會被貼到對話裡的東西。還沒切出標題時
        // 退回整份產出，讓這個鍵不會靜默地什麼都沒複製
        const ok = await copyText(replyText || raw)
        if (ok) toast.success(replyText ? '已複製建議回話' : '已複製草稿全文')
        else toast.error('沒有可複製的內容')
        return
      }
      const ok = await copyText(useSummaryStore.getState().text)
      if (ok) toast.success('已複製摘要 Markdown')
      else toast.error('沒有可複製的內容')
    }

    /** `[`／`]`：在收件匣目前那個分頁的清單裡走一格。 */
    const stepMention = (delta: 1 | -1) => {
      const { items, tab, selectedId, external } = useMentionsStore.getState()
      const visible = selectMentionsByState(items, tab)
      // 摘要工作台挑的那則（external）不在清單裡，用它當起點會直接算不出鄰居，
      // 所以退回 selectedId——它一定是清單裡的某一則或 null
      const from = visible.some((item) => item.id === external?.id)
        ? (external?.id ?? selectedId)
        : selectedId
      const next = stepId(visible, from, delta)
      if (next === null) return
      // 走 navigate 不直接 select()：URL 才是「在看哪一則」的唯一真相（§6.6），
      // 而 useRouteSync 的去重要看得到這次變動
      navigate(hashForMentions(next))
    }

    const focusSearch = (event: KeyboardEvent) => {
      const search = document.querySelector<HTMLInputElement>(
        'aside input[type="text"], aside input:not([type])',
      )
      if (!search) return
      event.preventDefault()
      search.focus()
      search.select()
    }

    const NAV: Partial<Record<NonNullable<HotkeyAction>, string>> = {
      'go-summary': hashForSummary(),
      'go-mentions': hashForMentions(),
      'go-settings': hashForSettings('reply'),
      'go-diagnostics': hashForSettings('diagnostics'),
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (isComposing(event)) return
      // 面板開著時它自己接管鍵盤，這裡完全不動作
      if (paletteOpen || helpOpen) return

      // 單獨按下修飾鍵不算一次「按鍵」，尤其不該把等待中的序列前綴清掉
      // （`g` 之後手指碰到 Shift，序列就斷了——使用者完全看不出原因）
      if (['Shift', 'Control', 'Alt', 'Meta'].includes(event.key)) return

      const action = resolveHotkey(event, isTypingTarget(event.target), pending.current)

      if (action === 'sequence') {
        event.preventDefault()
        pending.current = SEQUENCE_PREFIX
        if (timer.current !== null) window.clearTimeout(timer.current)
        timer.current = window.setTimeout(clearPending, SEQUENCE_TIMEOUT_MS)
        return
      }

      // 序列前綴之外的任何按鍵都把它清掉——兩鍵序列就是「相鄰的兩鍵」
      clearPending()
      if (!action) return

      const hash = NAV[action]
      if (hash) {
        event.preventDefault()
        navigate(hash)
        return
      }

      if (action === 'palette') {
        // Firefox 把 ⌘K／Ctrl+K 綁在搜尋列上，不 preventDefault 會被搶走
        event.preventDefault()
        setPaletteOpen(true)
        return
      }

      if (action === 'help') {
        event.preventDefault()
        setHelpOpen(true)
        return
      }

      if (action === 'toggle-evidence') {
        event.preventDefault()
        onToggleEvidence()
        return
      }

      if (action === 'generate') {
        event.preventDefault()
        startGenerate()
        return
      }

      if (action === 'stop') {
        // 只有真的停下了什麼才 preventDefault。沒在串流時 ⌘. 不該吃掉按鍵
        if (stopStream()) event.preventDefault()
        return
      }

      if (action === 'copy') {
        event.preventDefault()
        void copyMarkdown()
        return
      }

      if (action === 'prev-mention' || action === 'next-mention') {
        event.preventDefault()
        stepMention(action === 'next-mention' ? 1 : -1)
        return
      }

      if (action === 'focus-search') focusSearch(event)
    }

    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      clearPending()
    }
  }, [paletteOpen, helpOpen, setPaletteOpen, setHelpOpen, onToggleEvidence, route.section, navigate])
}
