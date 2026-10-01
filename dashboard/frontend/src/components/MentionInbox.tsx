import { useMemo, useRef } from 'react'
import { AlertCircleIcon, Loader2Icon, RefreshCwIcon } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { MentionCard } from '@/components/inbox/MentionCard'
import { MergeBar } from '@/components/inbox/MergeBar'
import { errorMessage } from '@/lib/api'
import { nextListIndex } from '@/lib/listNavigation'
import {
  MENTION_SORT_KEYS,
  MENTION_SORT_LABELS,
  mentionTruncation,
  sortMentions,
  type MentionSortKey,
} from '@/lib/listSort'
import { mergeBlockReason } from '@/lib/merge'
import { hashForMentions } from '@/lib/route'
import { useRouter } from '@/router/useRouter'
import { useViewerListSort } from '@/store/listSort'
import { selectMentionsByState, useMentionsStore } from '@/store/mentions'
import { useSpacesStore } from '@/store/spaces'
import { cn } from '@/lib/utils'
import type { MentionState } from '@/lib/types'

interface MentionInboxProps {
  onSelect: (mentionId: number) => void
  /** 合併產生草稿：主要那則 ＋ 一起回的其他幾則 */
  onMergedGenerate: (primaryId: number, mergeIds: number[]) => void
}

export function MentionInbox({ onSelect, onMergedGenerate }: MentionInboxProps) {
  const items = useMentionsStore((state) => state.items)
  const counts = useMentionsStore((state) => state.counts)
  const tab = useMentionsStore((state) => state.tab)
  const loading = useMentionsStore((state) => state.loading)
  const loaded = useMentionsStore((state) => state.loaded)
  const checking = useMentionsStore((state) => state.checking)
  const error = useMentionsStore((state) => state.error)
  const selectedId = useMentionsStore((state) => state.selectedId)
  const mergeIds = useMentionsStore((state) => state.mergeIds)
  const setTab = useMentionsStore((state) => state.setTab)
  const checkNow = useMentionsStore((state) => state.checkNow)
  const setMentionState = useMentionsStore((state) => state.setMentionState)
  const toggleMerge = useMentionsStore((state) => state.toggleMerge)
  const clearMerge = useMentionsStore((state) => state.clearMerge)
  const spaces = useSpacesStore((state) => state.items)
  const { navigate } = useRouter()
  const listRef = useRef<HTMLUListElement>(null)

  /**
   * 勾選改動之後把結果寫回網址（設計規格 §6.6）。
   *
   * 讀 getState() 而不是自己算下一份清單：`toggleMerge` 與 `setTab` 各自
   * 還有別的副作用（勾第一則會順便設 selectedId、換頁籤會清空勾選），
   * 在這裡重算一次等於把那些規則抄第二份。zustand 的 set 是同步的，
   * 呼叫完立刻讀得到新值。
   *
   * 一律 replace：勾選不是「值得用返回鍵走回去」的導覽，每勾一下就推一筆
   * 歷史的話，要按好幾次返回鍵才離得開收件匣。
   */
  const syncMergeToUrl = () => {
    const { mergeIds: next, selectedId: current } = useMentionsStore.getState()
    navigate(hashForMentions(next[0] ?? current, next), { replace: true })
  }

  // 載入與 45 秒輪詢已經搬進 store，由 AppShell 在登入後啟動一次
  // （設計規格 §7.4）——輪詢屬於這份資料，不屬於這個畫面。

  const { prefs, setMentionSort } = useViewerListSort()
  const mentionSort = prefs.mention

  // 快捷鍵 `[`／`]`（useGlobalHotkeys）用同一條組合算「下一則」，兩邊要一致
  const visible = useMemo(
    () => sortMentions(selectMentionsByState(items, tab), mentionSort),
    [items, tab, mentionSort],
  )

  // 勾選的順序就是「誰是主要那則」：回話會送到第一則所在的討論串
  const selected = useMemo(
    () => mergeIds.map((id) => items.find((m) => m.id === id)).filter((m) => m !== undefined),
    [mergeIds, items],
  )
  const merging = selected.length > 0

  // 收件匣最多只載入最新 200 筆（見 mentionTruncation 的註解）。
  // 截斷時「新→舊」仍然正確（截的正好是最新那段），其他排序只涵蓋已載入的部分
  const truncation = useMemo(() => mentionTruncation(counts, items.length), [counts, items.length])

  const handleCheckNow = async () => {
    const stats = await checkNow()
    if (stats) {
      toast.success(
        `已完成一輪採集：新增 ${stats.new_mentions ?? 0} 則，掃描 ${stats.spaces_polled ?? 0}/${stats.spaces_total ?? 0} 個 Space`,
      )
    }
  }

  /**
   * 清單的 ↑↓ Home End PageUp PageDown（設計規格 §11.1 的「左欄清單移動」）。
   *
   * **只移動焦點，不改選取**：開啟那一則交給 Enter／空白鍵（卡片本身就是
   * `<button>`，瀏覽器原生處理），與 `SpaceList` 一致。每按一次方向鍵就
   * 導覽的話，走過十則就在歷史裡留下十筆、而且每一則都會重掛草稿工作區。
   * 想快速換一則有 `[`／`]`，那兩個鍵才是「換 Mention」。
   *
   * **不做 roving tabindex**：§10.5 那套是為 436 筆的虛擬清單設計的（active
   * 那一列可能不在 DOM 裡）。這裡是幾十筆的一般清單，全部都在 DOM 裡，
   * 而且每張卡片還有勾選框與狀態鈕——把它們排除在 tab 序之外反而更難用。
   * 這裡是純加法：Tab 的行為完全沒動，只是多了方向鍵這條快路。
   */
  const onListKeyDown = (event: React.KeyboardEvent) => {
    const card = (event.target as HTMLElement).closest?.('[data-mention-index]')
    // 焦點在勾選框或狀態鈕上時 card 是 null——方向鍵不該從那裡跳走
    if (!card) return
    const from = Number(card.getAttribute('data-mention-index'))
    if (!Number.isInteger(from)) return

    const next = nextListIndex(event.key, from, visible.length)
    if (next === null) return // 不是導航鍵就交還給瀏覽器
    event.preventDefault()
    listRef.current
      ?.querySelector<HTMLElement>(`[data-mention-index="${next}"]`)
      ?.focus()
  }

  const handleToggleState = async (id: number, next: MentionState) => {
    try {
      await setMentionState(id, next)
      toast.success(next === 'resolved' ? '已標記為已處理' : '已退回待處理')
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/*
        2026-10-01 改版 S5：tabs 與「立即檢查」同一列、線型 tabs，與摘要工作台
        （SpacesTable）、工時草稿（WorklogTable）的頂部同一個語法。
        模組標題已經在 TopBar 的 h1，這裡的 h2 只留給螢幕報讀的標題導覽——
        拿掉它會讓用標題跳段的人找不到清單的起點。

        ⚠️ 規格的第三個 tab「已有 Draft Reply」刻意沒做：資料有（Mention 的
        `has_draft`），但分頁的歸類同時被 `[`／`]` 快捷鍵（useGlobalHotkeys）拿來
        算「下一則」，只在這裡多一個篩選，按 `]` 就會跳到畫面上看不到的那則。
        要做就得一起改 store 的 `tab` 型別與 selectMentionsByState，這次不在範圍內。
      */}
      <div className="shrink-0 space-y-2.5 px-3 pt-2 pb-2.5">
        <h2 className="sr-only">Mention 收件匣</h2>
        <div className="flex flex-wrap items-center gap-2 border-b border-line">
          <Tabs
            value={tab}
            onValueChange={(value) => {
              // setTab 會清掉勾選（已處理那頁勾起來合併沒有意義），網址要跟上
              setTab(value as MentionState)
              syncMergeToUrl()
            }}
          >
            <TabsList variant="line" aria-label="Mention 子視圖">
              <TabsTrigger value="pending">
                待處理
                {counts.pending > 0 ? (
                  <span className="metric ml-1 inline-flex min-w-4 items-center justify-center rounded-full bg-signal px-1 text-2xs font-semibold text-signal-on">
                    {counts.pending}
                  </span>
                ) : null}
              </TabsTrigger>
              <TabsTrigger value="resolved">
                已處理
                <span className="metric ml-1 text-2xs text-fg-dim">{counts.resolved}</span>
              </TabsTrigger>
            </TabsList>
          </Tabs>
          <Button
            size="xs"
            variant="ghost"
            className="ml-auto"
            onClick={() => void handleCheckNow()}
            disabled={checking}
          >
            {checking ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />}
            立即檢查
          </Button>
        </div>

        <Select
          items={MENTION_SORT_LABELS}
          value={mentionSort}
          onValueChange={(next) => {
            // Base UI 的 Select 清除選擇時會給 null；那不是「換一種排序」，不動
            if (!next) return
            setMentionSort(next as MentionSortKey)
          }}
        >
          <SelectTrigger size="sm" className="h-7 w-full text-xs" aria-label="Mention 排序方式">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {MENTION_SORT_KEYS.map((key) => (
              <SelectItem key={key} value={key}>
                {MENTION_SORT_LABELS[key]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* 放在排序控制項正下方、清單上方：這句話是在限定「這份排序」的意義，
            放清單底部的話，Viewer 要捲完 200 筆才知道前面的順序不是全部 */}
        {truncation ? (
          <p
            role="status"
            data-testid="mention-truncation"
            data-loaded={truncation.loaded}
            data-total={truncation.total}
            // 讓測試斷言「有沒有多說那一句」而不必斷言措辭
            data-partial-sort={mentionSort !== 'time-desc' ? 'true' : 'false'}
            className="flex items-start gap-1.5 text-2xs leading-snug text-caution"
          >
            <AlertCircleIcon className="mt-px size-3 shrink-0" aria-hidden />
            <span>
              只載入最新 {truncation.loaded} 筆，共 {truncation.total} 筆。
              {mentionSort !== 'time-desc'
                ? `目前的排序只涵蓋已載入的 ${truncation.loaded} 筆，不是全部。`
                : null}
            </span>
          </p>
        ) : null}
      </div>

      {error ? (
        // 與 SpacesTable／WorklogTable 的錯誤框同一個樣式：清單改成內縮的卡片之後，
        // 滿版的色帶會變成版面上唯一一條貼邊的東西
        <div className="mx-3 mb-2.5 flex shrink-0 items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <AlertCircleIcon className="mt-0.5 size-3 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      {/* 合併列只在真的勾了東西時才出現——常駐一條工具列會讓「單則回覆」
          這個絕大多數的情況每次都要多看一行 */}
      {merging ? (
        <MergeBar
          selected={selected}
          onCancel={() => {
            clearMerge()
            syncMergeToUrl()
          }}
          onGenerate={() => onMergedGenerate(mergeIds[0], mergeIds)}
        />
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        {/* ⚠️ 「從未載入過」要當成載入中，不是「沒有」：第一次 load() 在
            useBootstrap 的 effect 裡才發出，收件匣第一次 render 時 loading 還是
            false（見 store 的 `loaded`）。載入失敗而從未載入過時兩個都不畫，
            只留上面的錯誤框——不知道就說不知道。 */}
        {(loading && items.length === 0) || (!loaded && !error && visible.length === 0) ? (
          <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
            <Loader2Icon className="size-3.5 animate-spin" />
            正在載入…
          </div>
        ) : null}

        {!loading && loaded && visible.length === 0 ? (
          <p className="px-2 py-8 text-center text-xs text-muted-foreground">
            {tab === 'pending' ? '沒有待處理的 Mention。' : '還沒有已處理的 Mention。'}
          </p>
        ) : null}

        {/* 一張卡片裝整份清單、列與列之間用分隔線（設計稿的主從式收件匣），
            不再是一則一張浮起來的卡片：幾十張各自帶框的卡片疊在一起，
            讀起來是一堆碎片，而不是「一份清單」。清單空的時候不畫這張空框 */}
        <ul
          ref={listRef}
          onKeyDown={onListKeyDown}
          className={cn(
            'divide-y divide-line overflow-hidden rounded-xl border-line bg-surface',
            visible.length > 0 && 'border',
          )}
        >
          {visible.map((mention, index) => {
            // 已處理那一頁不提供合併（回過的東西沒有「一起回」可言）
            const blocked =
              tab === 'pending' ? mergeBlockReason(mention, selected, spaces) : 'other-space'
            const checked = mergeIds.includes(mention.id)
            const selectable = tab === 'pending' && (checked || !blocked)
            return (
              <li key={mention.id}>
                <MentionCard
                  mention={mention}
                  index={index}
                  active={selectedId === mention.id}
                  checked={checked}
                  mergeable={tab === 'pending'}
                  selectable={selectable}
                  blocked={blocked}
                  dimmed={merging && !selectable}
                  onSelect={() => onSelect(mention.id)}
                  onToggleMerge={() => {
                    toggleMerge(mention.id)
                    syncMergeToUrl()
                  }}
                  onToggleState={(next) => void handleToggleState(mention.id, next)}
                />
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}
