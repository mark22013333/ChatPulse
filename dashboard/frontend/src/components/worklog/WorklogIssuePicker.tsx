import { useEffect, useMemo, useRef, useState } from 'react'
import { Combobox } from '@base-ui/react/combobox'
import { CheckIcon, ChevronDownIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { api, errorMessage } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { WorklogCandidate, WorklogDayEntry, WorklogIssueSearchResponse } from '@/lib/types'
import {
  CLEARED_ASSIGNMENT,
  currentAssignmentLabel,
  normalizeIssueQuery,
  splitCandidates,
  toAssignment,
  type IssueLookupStatus,
  type WorklogAssignment,
} from './issueChoice'

const CONFIDENCE_LABEL: Record<string, string> = { strong: '強', medium: '中', weak: '弱' }

/** 打字停多久才送出搜尋。每打一個字就打一次 ZPlanner 會讓結果亂序閃爍，也白費配額。 */
export const ISSUE_SEARCH_DEBOUNCE_MS = 300

/** 選單裡的一項。`id` 只用來比對「同一項」，不是 issue id 的唯一來源。 */
interface PickerItem {
  id: string
  /** `unassigned`＝「未指派」；其餘都是一張單 */
  kind: 'unassigned' | 'issue'
  label: string
  /** 第二行的說明（信心度與理由、或專案代號）。沒有就不畫 */
  detail: string | null
  assignment: WorklogAssignment
}

interface PickerGroup {
  value: string
  items: PickerItem[]
}

const UNASSIGNED_ITEM: PickerItem = {
  id: '__unassigned__',
  kind: 'unassigned',
  label: '未指派',
  detail: null,
  assignment: CLEARED_ASSIGNMENT,
}

function issueLabel(issue: { issue_key: string; title: string }): string {
  return issue.title ? `${issue.issue_key}　${issue.title}` : issue.issue_key
}

function candidateItem(candidate: WorklogCandidate): PickerItem {
  const confidence = CONFIDENCE_LABEL[candidate.confidence] ?? candidate.confidence
  const permission = candidate.can_worklog === false ? '（可能沒有填工時的權限）' : ''
  return {
    id: `issue:${candidate.issue_id}`,
    kind: 'issue',
    label: issueLabel(candidate),
    // 純記憶（沒被這次搜到）不標信心度：那只是「上次選過」，不是證據。合併過的
    // （matched_source 有值）照搜到那一邊的信心度標，理由兩句後端已經接好
    detail:
      candidate.source === 'last_used' && !candidate.matched_source
        ? `${candidate.reason}${permission}`
        : `信心${confidence}：${candidate.reason}${permission}`,
    assignment: toAssignment(candidate),
  }
}

type SearchState =
  | { kind: 'idle' }
  | { kind: 'loading'; query: string }
  | { kind: 'done'; query: string; result: WorklogIssueSearchResponse }
  | { kind: 'error'; query: string; message: string }

/**
 * 「記到哪張單」的可輸入選單（Base UI Combobox，搜尋框放在 popup 裡）。
 *
 * ### 🔴 歸屬一律從「未指派」開始（ADR-0008）
 *
 * 觸發鈕顯示的只看 entry 的四欄：`issue_id` 是 `null` 就是「未指派」。**「上次記到」
 * 與排第一的候選都不准預選**——它們只是選單最上面的選項，要 Viewer 點了才會呼叫
 * `onPick`。一份看起來合理的自動歸屬，代價由一個月後發現填錯的人支付。
 *
 * ### 為什麼搜尋框放在 popup 裡、不直接把觸發鈕做成輸入框
 *
 * 輸入框同時當「顯示目前選擇」與「打字搜尋」時，打到一半的關鍵字與已選的單號會
 * 搶同一個位置：Viewer 打了「PEI」然後點別處，畫面上留著「PEI」卻什麼都沒選，
 * 看起來像選了。分開之後，觸發鈕永遠只說目前存的是什麼。
 *
 * ### 選單內容
 *
 * - 關鍵字是空的：「上次記到」→ 系統候選 →（目前的選擇不在候選裡時）目前的選擇 → 未指派。
 * - 有關鍵字：搜尋全部可記工時的單，依專案分組。**空白不送出**（ZPlanner 對空字串
 *   回 0 筆而不是全部，見 `normalizeIssueQuery`）。
 * - ZPlanner 不可用：搜尋框停用（`status.kind === 'unavailable'`），候選仍可點——
 *   「上次記到」是本地記憶，選它不需要 ZPlanner。
 */
export function WorklogIssuePicker({
  entry,
  status,
  disabled = false,
  onPick,
  search = api.searchWorklogIssues,
  retryRequest = 0,
}: {
  entry: WorklogDayEntry
  status: IssueLookupStatus
  disabled?: boolean
  /** Viewer 點了一項。四欄一律一起給。 */
  onPick: (assignment: WorklogAssignment) => void
  /** 測試注入用；預設打 `GET /worklogs/issue-search` */
  search?: (q: string, signal?: AbortSignal) => Promise<WorklogIssueSearchResponse>
  /**
   * 每次遞增就用失敗的那個關鍵字重查一次並打開選單（卡片上的「重試」鈕）。
   * 用計數而不是布林：連按兩次重試要能查兩次。
   */
  retryRequest?: number
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [searchState, setSearchState] = useState<SearchState>({ kind: 'idle' })
  const controllerRef = useRef<AbortController | null>(null)
  /** 「重試」已經直接查過這個關鍵字了，接下來那次 query 變化不要再經 debounce 查第二次 */
  const alreadySearchedRef = useRef<string | null>(null)
  const searchDisabled = status.kind === 'unavailable'

  // 卸載時中止在飛的搜尋，避免對已卸載的元件 setState
  useEffect(() => () => controllerRef.current?.abort(), [])

  const runSearch = (raw: string) => {
    controllerRef.current?.abort()
    const keyword = normalizeIssueQuery(raw)
    if (keyword === null || searchDisabled) {
      controllerRef.current = null
      setSearchState({ kind: 'idle' })
      return
    }
    const controller = new AbortController()
    controllerRef.current = controller
    setSearchState({ kind: 'loading', query: keyword })
    search(keyword, controller.signal).then(
      (result) => {
        if (!controller.signal.aborted) setSearchState({ kind: 'done', query: keyword, result })
      },
      (err: unknown) => {
        if (controller.signal.aborted || (err as Error)?.name === 'AbortError') return
        setSearchState({ kind: 'error', query: keyword, message: errorMessage(err) })
      },
    )
  }

  // 打字停 ISSUE_SEARCH_DEBOUNCE_MS 才送；關鍵字變空就回到候選清單
  useEffect(() => {
    if (normalizeIssueQuery(query) === null) {
      controllerRef.current?.abort()
      setSearchState({ kind: 'idle' })
      return
    }
    if (alreadySearchedRef.current === query) {
      alreadySearchedRef.current = null
      return
    }
    const timer = setTimeout(() => runSearch(query), ISSUE_SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(timer)
    // 刻意只依賴 query：runSearch 每次 render 都是新函式，放進依賴會讓每次重畫都重設計時器
  }, [query])

  // 卡片上的「重試」：用那次失敗的關鍵字直接查，不等 debounce
  const failedTerm = status.kind === 'query-failed' ? status.term : null
  useEffect(() => {
    if (retryRequest === 0) return
    const term = failedTerm ?? entry.label ?? ''
    alreadySearchedRef.current = term
    setQuery(term)
    setOpen(true)
    runSearch(term)
    // 刻意只依賴 retryRequest：只有「按了重試」才重查，status 或 entry 換了不算
  }, [retryRequest])

  const current: PickerItem = useMemo(() => {
    const label = currentAssignmentLabel(entry)
    if (label === null) return UNASSIGNED_ITEM
    return {
      id: `issue:${entry.issue_id}`,
      kind: 'issue',
      label,
      detail: null,
      assignment: {
        issue_id: entry.issue_id,
        issue_key: entry.issue_key,
        project_id: entry.project_id,
        project_key: entry.project_key,
      },
    }
  }, [entry])

  const groups: PickerGroup[] = useMemo(() => {
    if (searchState.kind === 'done' && normalizeIssueQuery(query) !== null) {
      return searchState.result.groups.map((group) => ({
        value: group.project_key ?? (group.project_id !== null ? `專案 #${group.project_id}` : '未標專案'),
        items: group.issues.map((issue) => ({
          id: `issue:${issue.issue_id}`,
          kind: 'issue' as const,
          label: issueLabel(issue),
          detail: null,
          assignment: toAssignment(issue),
        })),
      }))
    }
    if (normalizeIssueQuery(query) !== null) return [] // 搜尋中或失敗：清單留空，狀態列說明
    const { lastUsed, others } = splitCandidates(entry.candidates)
    const out: PickerGroup[] = []
    if (lastUsed) out.push({ value: '上次記到', items: [candidateItem(lastUsed)] })
    if (others.length > 0) out.push({ value: '系統候選', items: others.map(candidateItem) })
    const listed = new Set(out.flatMap((g) => g.items.map((i) => i.id)))
    if (current.kind === 'issue' && !listed.has(current.id)) {
      // 目前的選擇不在候選裡（例如之前搜尋選的）也要列出來，否則看起來像沒選
      out.push({ value: '目前的選擇', items: [current] })
    }
    out.push({ value: '不記到任何單', items: [UNASSIGNED_ITEM] })
    return out
  }, [entry.candidates, current, query, searchState])

  const statusText = (() => {
    if (normalizeIssueQuery(query) === null) return null
    if (searchState.kind === 'loading') return '搜尋中…'
    if (searchState.kind === 'error') return `搜尋失敗：${searchState.message}`
    if (searchState.kind === 'done' && searchState.result.count === 0) {
      return `可記工時的單裡找不到「${searchState.query}」。換個關鍵字或直接輸入單號試試。`
    }
    return null
  })()

  return (
    <Combobox.Root<PickerItem>
      items={groups}
      value={current}
      filter={null}
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) setQuery('')
      }}
      inputValue={query}
      onInputValueChange={(next, details) => {
        // 點選項目時 Base UI 會把輸入框改成那一項的文字——那不是 Viewer 在打字，
        // 拿去搜尋只會多打一次 ZPlanner、還把清單換成搜尋結果
        if (details?.reason === 'item-press') return
        setQuery(next)
      }}
      itemToStringLabel={(item) => item?.label ?? ''}
      isItemEqualToValue={(item, value) => item?.id === value?.id}
      onValueChange={(next) => {
        // ⚠️ Base UI 清除選擇時給 null。那**不是**「改成未指派」：Viewer 沒有點任何
        // 一項，當成清除會安靜地抹掉他選好的單。明確點「未指派」才清除。
        if (!next) return
        if (next.id === current.id) return
        onPick(next.assignment)
      }}
      disabled={disabled}
    >
      <Combobox.Trigger
        aria-label="記到哪張單"
        className={cn(
          'border-input flex h-7 w-full min-w-0 items-center justify-between gap-1.5 rounded-lg border bg-transparent py-1 pr-2 pl-2.5 text-left text-sm outline-none',
          'focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-3 disabled:cursor-not-allowed disabled:opacity-50',
        )}
      >
        <span className={cn('min-w-0 flex-1 truncate', current.kind === 'unassigned' ? 'text-muted-foreground' : null)}>
          {current.label}
        </span>
        <ChevronDownIcon className="text-muted-foreground pointer-events-none size-4 shrink-0" />
      </Combobox.Trigger>
      <Combobox.Portal>
        <Combobox.Positioner align="start" sideOffset={4} className="isolate z-50">
          <Combobox.Popup
            aria-label="選擇要記到哪張單"
            className="bg-popover text-popover-foreground ring-foreground/10 max-h-(--available-height) w-auto max-w-96 min-w-72 overflow-hidden rounded-lg shadow-md ring-1"
          >
            <div className="border-line border-b p-1.5">
              <Combobox.Input
                aria-label="搜尋可記工時的單"
                disabled={searchDisabled}
                placeholder={searchDisabled ? 'ZPlanner 不可用，無法搜尋' : '輸入關鍵字或單號搜尋全部可記工時的單'}
                className="border-input h-7 w-full rounded-md border bg-transparent px-2 text-sm outline-none disabled:cursor-not-allowed disabled:opacity-50"
              />
            </div>
            <Combobox.Status className="text-fg-dim px-2.5 py-1.5 text-xs empty:hidden">
              {statusText}
              {searchState.kind === 'error' ? (
                <Button size="xs" variant="outline" className="ml-2" onClick={() => runSearch(query)}>
                  重試
                </Button>
              ) : null}
            </Combobox.Status>
            <Combobox.List className="max-h-80 overflow-y-auto p-1">
              {(group: PickerGroup) => (
                <Combobox.Group key={group.value} items={group.items} className="py-0.5">
                  <Combobox.GroupLabel className="text-muted-foreground px-1.5 py-1 text-xs">
                    {group.value}
                  </Combobox.GroupLabel>
                  <Combobox.Collection>
                    {(item: PickerItem) => (
                      <Combobox.Item
                        key={item.id}
                        value={item}
                        className="data-highlighted:bg-accent data-highlighted:text-accent-foreground relative flex w-full cursor-default items-start gap-1.5 rounded-md py-1 pr-7 pl-1.5 text-sm outline-none select-none"
                      >
                        <span className="flex min-w-0 flex-col gap-0.5 whitespace-normal">
                          <span className={item.kind === 'unassigned' ? 'text-xs' : 'font-medium'}>{item.label}</span>
                          {/* 信心度只有三級並一律附理由：讓人讀理由，不是讀名次 */}
                          {item.detail ? (
                            <span className="text-2xs text-muted-foreground leading-snug">{item.detail}</span>
                          ) : null}
                        </span>
                        <Combobox.ItemIndicator className="absolute top-1.5 right-2">
                          <CheckIcon className="size-4" />
                        </Combobox.ItemIndicator>
                      </Combobox.Item>
                    )}
                  </Combobox.Collection>
                </Combobox.Group>
              )}
            </Combobox.List>
          </Combobox.Popup>
        </Combobox.Positioner>
      </Combobox.Portal>
    </Combobox.Root>
  )
}
