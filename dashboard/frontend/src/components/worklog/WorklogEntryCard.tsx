import { useEffect, useId, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { WorklogIssuePicker } from '@/components/worklog/WorklogIssuePicker'
import { issueLookupStatus, splitCandidates } from '@/components/worklog/issueChoice'
import { cn } from '@/lib/utils'
import { formatHours } from '@/store/worklog'
import { entryStateText, isEntryLocked, type WorklogEntryPatch, type WorklogEntryTone } from '@/store/worklogDay'
import type { WorklogDayEntry, WorklogIssueSearchResponse } from '@/lib/types'

const TONE_CLASS: Record<WorklogEntryTone, string> = {
  neutral: 'text-fg-dim',
  ok: 'text-verified',
  caution: 'text-caution',
  danger: 'text-destructive',
}

/**
 * 一張草稿卡＝草稿裡的一筆工時（AC-3、D-16）。
 *
 * ### 🔴 「記到哪張單」一律從「未指派」開始（ADR-0008）
 *
 * `issue_id` 是 `null` 就顯示「未指派」，**候選排第一的、「上次記到」的都不准預選**。
 * 一份看起來合理的自動歸屬，代價由一個月後發現填錯的人支付（月結後要走
 * correction-request）。選單本身在 `WorklogIssuePicker`，那裡也處理了 Base UI
 * 清除選擇時給 `null` 的陷阱。
 *
 * 欄位以前叫「專案歸屬」，但能選的其實是 ZPlanner 的**單**（issue），專案只是單的
 * 所屬——Viewer 讀到「專案」會以為選一個專案就好，2026-10-01 改名。
 *
 * ### 候選為什麼是空的，要講清楚是哪一種（`issueLookupStatus`）
 *
 * ZPlanner 不可用、這一段的查詢失敗、有查但沒命中——三種情況 Viewer 的下一步
 * 不同（找管理者／重試／自己搜），以前共用同一句話。
 *
 * ### 存檔時機「確認就存」（D-16）
 *
 * - 歸屬：一選就存。
 * - 時數、備註：blur 或 Enter 時存，每次只帶這一筆、只帶改了的那個欄位。
 * - 被否決的做法：debounce（計時器與送出、重產搶時序）、整頁儲存鈕（畫面與 DB
 *   的空窗取決於 Viewer 記不記得按，而 submit 讀的是 DB）。
 * - 打了字還沒確認的欄位會回報給容器（`onDirtyChange`），容器據此停用送出鈕。
 * - 存檔失敗**保留 Viewer 的輸入**並在卡上顯示錯誤；此時欄位仍算「沒存好」，
 *   送出鈕也維持停用——畫面上的值不在 DB 裡，送出去的會是另一個數字。
 *
 * 已經送進 ZPlanner 的筆（有 worklog id）整張唯讀：後端對這種筆的 PUT 一律 409（D-21）。
 */
export function WorklogEntryCard({
  entry,
  generationMeta = null,
  saving = false,
  error = null,
  onSave,
  onDirtyChange,
  searchIssues,
}: {
  entry: WorklogDayEntry
  /**
   * 這份草稿的 `generation_meta`（GET 回應原樣）。卡片從裡面的 `zplanner` 段分辨
   * 「不可用／查詢失敗／沒命中」；給 `null` 時不說是哪一種，搜尋框照常可用。
   */
  generationMeta?: unknown
  saving?: boolean
  error?: string | null
  /**
   * 存這一筆。簽章帶 `id` 是刻意的：容器可以直接傳 store 的 `updateEntry`
   * （參照穩定），不必每張卡包一個新的箭頭函式。
   */
  onSave: (id: number, patch: WorklogEntryPatch) => Promise<boolean> | void
  /** 同上，可直接傳 store 的 `setDirty`。 */
  onDirtyChange?: (id: number, dirty: boolean) => void
  /** 測試注入用；省略時打 `GET /worklogs/issue-search` */
  searchIssues?: (q: string, signal?: AbortSignal) => Promise<WorklogIssueSearchResponse>
}) {
  const locked = isEntryLocked(entry)
  const status = entryStateText(entry)
  const hoursId = useId()
  const noteId = useId()
  const [retryRequest, setRetryRequest] = useState(0)

  const [hours, setHours] = useState(entry.hours ?? '')
  const [note, setNote] = useState(entry.note ?? '')

  // 伺服器的值換了（存檔成功、重讀、重新產生）才跟著換。依賴是欄位值本身，
  // 所以別張卡存檔造成的整份取代不會蓋掉這張卡上正在打的字。
  useEffect(() => setHours(entry.hours ?? ''), [entry.hours])
  useEffect(() => setNote(entry.note ?? ''), [entry.note])

  const entryId = entry.id
  const dirty = hours !== (entry.hours ?? '') || note !== (entry.note ?? '')
  useEffect(() => {
    onDirtyChange?.(entryId, dirty)
  }, [entryId, dirty, onDirtyChange])
  // 卸載時撤銷髒值標記，否則換一天之後送出鈕會被一張已經不在的卡擋住。
  // ⚠️ 依賴裡的 onDirtyChange 若每次 render 都是新函式，這個 cleanup 會每次都
  // 先清成 false 再由上一個 effect 設回來——結果仍然正確，只是多做工；所以
  // 簽章設計成可以直接傳 store 裡參照穩定的 action。
  useEffect(() => () => onDirtyChange?.(entryId, false), [entryId, onDirtyChange])

  const commitHours = () => {
    const next = hours.trim() === '' ? null : hours.trim()
    if (next === (entry.hours ?? null)) {
      if (hours !== (entry.hours ?? '')) setHours(entry.hours ?? '') // 只差空白
      return
    }
    void onSave(entry.id, { hours: next })
  }

  const commitNote = () => {
    const next = note === '' ? null : note
    if (next === (entry.note ?? null)) return
    void onSave(entry.id, { note: next })
  }

  /**
   * Enter 以 blur 觸發存檔，而不是直接呼叫 commit——直接呼叫的話，接著的 blur
   * 會再存一次（那時 entry 還是舊值，比對不出已經存過）。
   *
   * ⚠️ 注音／倉頡選字時按的 Enter 也會觸發 keydown。不擋 `isComposing` 的話，
   * 選字的那一下就把打到一半的備註存進去了。
   */
  const blurOnEnter = (event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>, allowShift: boolean) => {
    if (event.key !== 'Enter' || event.nativeEvent.isComposing) return
    if (allowShift && event.shiftKey) return
    event.preventDefault()
    event.currentTarget.blur()
  }

  const lookup = issueLookupStatus(generationMeta, entry.cluster_key, entry.candidates)
  const hasLastUsed = splitCandidates(entry.candidates).lastUsed !== null

  return (
    <article
      aria-label={`第 ${entry.seq} 筆草稿`}
      aria-busy={saving || undefined}
      className={cn(
        'border-line rounded-lg border p-3',
        error ? 'border-destructive/50' : null,
        entry.issue_id === null && !locked ? 'border-caution-line border-dashed' : null,
      )}
    >
      <header className="flex items-baseline gap-2">
        <span className="metric text-fg-subtle text-xs">#{entry.seq}</span>
        <h3 className="min-w-0 flex-1 truncate text-sm font-medium">{entry.label ?? '（沒有分群資訊）'}</h3>
        <span className={cn('shrink-0 text-xs', TONE_CLASS[status.tone])}>{status.label}</span>
      </header>
      {/* 分群是啟發式，猜錯時 Viewer 唯一能察覺的方式就是讀這句話。
          對不到分群（D-9）時就說對不到，不要留白讓人以為沒有理由 */}
      <p className="text-fg-dim mt-1 text-xs leading-relaxed">
        {entry.reason ?? '這一筆對不到目前的分群資訊（可能是草稿重新產生過），請看備註與證據判斷。'}
      </p>
      {status.hint ? (
        <p className={cn('mt-1 text-xs leading-relaxed', TONE_CLASS[status.tone])}>{status.hint}</p>
      ) : null}

      <div className="mt-3 grid gap-3 sm:grid-cols-[1fr_7rem]">
        <div className="flex min-w-0 flex-col gap-1">
          {/* 觸發鈕自帶可及名稱「記到哪張單」，這裡的字只是視覺標籤 */}
          <span className="text-fg-dim text-xs" aria-hidden>
            記到哪張單
          </span>
          <WorklogIssuePicker
            entry={entry}
            status={lookup}
            disabled={locked}
            onPick={(assignment) => void onSave(entry.id, assignment)}
            search={searchIssues}
            retryRequest={retryRequest}
          />
          {!locked ? <LookupHint status={lookup} hasLastUsed={hasLastUsed} onRetry={() => setRetryRequest((n) => n + 1)} /> : null}
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor={hoursId} className="text-fg-dim text-xs">
            時數
          </label>
          <Input
            id={hoursId}
            aria-label="時數"
            inputMode="decimal"
            className="metric"
            value={hours}
            placeholder="—"
            disabled={locked}
            onChange={(event) => setHours(event.target.value)}
            onBlur={commitHours}
            onKeyDown={(event) => blurOnEnter(event, false)}
          />
          {entry.allocated_hours !== null ? (
            // 系統原始分配與人改過的值分開顯示——「這個數字是誰決定的」是回頭
            // 檢查工時時最想知道的事；湊整的 0.5 落在誰身上也要看得見
            <p className="text-fg-subtle text-2xs">
              系統分配 {formatHours(entry.allocated_hours)}
              {entry.rounded_up ? '（湊整 +0.5）' : ''}
            </p>
          ) : null}
        </div>
      </div>

      <div className="mt-3 flex flex-col gap-1">
        <label htmlFor={noteId} className="text-fg-dim text-xs">
          備註
        </label>
        <Textarea
          id={noteId}
          aria-label="備註"
          rows={2}
          value={note}
          disabled={locked}
          onChange={(event) => setNote(event.target.value)}
          onBlur={commitNote}
          onKeyDown={(event) => blurOnEnter(event, true)}
        />
      </div>

      {saving ? (
        <p role="status" className="text-fg-subtle mt-2 text-xs">
          存檔中…
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="text-destructive mt-2 text-xs leading-relaxed">
          沒有存成功：{error}　你輸入的內容還在，改好後按 Enter 或點到別處再存一次。
        </p>
      ) : null}
    </article>
  )
}

/**
 * 候選清單下方那一行：為什麼候選是這個樣子、下一步可以做什麼。
 *
 * 三種「沒有系統候選」各講各的（見 `issueLookupStatus`）。有候選時不說話——
 * 理由已經寫在每一個選項裡了。
 */
function LookupHint({
  status,
  hasLastUsed,
  onRetry,
}: {
  status: ReturnType<typeof issueLookupStatus>
  hasLastUsed: boolean
  onRetry: () => void
}) {
  if (status.kind === 'unavailable') {
    return (
      <p role="note" data-lookup="unavailable" className="text-caution text-2xs leading-relaxed">
        ZPlanner 目前不可用{status.reason ? `：${status.reason}` : ''}。搜尋已停用
        {hasLastUsed ? '，「上次記到」仍可點選' : ''}；這不是你設定錯了，請找部署這個服務的人處理。
      </p>
    )
  }
  if (status.kind === 'query-failed') {
    return (
      <div role="note" data-lookup="query-failed" className="text-destructive text-2xs leading-relaxed">
        <span>
          這一段的候選查詢失敗了{status.reason ? `：${status.reason}` : ''}。這不代表沒有相符的單。
        </span>
        <Button size="xs" variant="outline" className="ml-2" onClick={onRetry}>
          重試查詢
        </Button>
      </div>
    )
  }
  if (status.kind === 'no-match') {
    return (
      <p role="note" data-lookup="no-match" className="text-fg-subtle text-2xs leading-relaxed">
        系統用這一段的關鍵字沒有搜到相符的單。點開選單，輸入關鍵字或單號自己搜。
      </p>
    )
  }
  return null
}
