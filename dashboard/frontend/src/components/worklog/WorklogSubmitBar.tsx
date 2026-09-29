import { useState } from 'react'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { Button } from '@/components/ui/button'
import { formatHours } from '@/store/worklog'
import {
  daySubmitTotals,
  isEntryLocked,
  submitErrorText,
  submitSummaryLines,
  type WorklogDayError,
} from '@/store/worklogDay'
import type { WorklogDay, WorklogSubmitSummary } from '@/lib/types'

/**
 * 送出區（AC-10、AC-11）：把這一天的草稿逐筆寫進 ZPlanner。
 *
 * ### 🔴 三件會讓人重複填工時的事
 *
 * 1. **HTTP 200 不代表每一筆都成功。** 逐筆狀態畫在各張卡上（混合狀態），這裡
 *    只放整天的摘要。把 200 當成「全部送出」會讓失敗的那幾筆沒人回來補；把有筆
 *    失敗當成「整批沒送」則會讓人再按一次，已成功的那幾筆就重複了。
 * 2. **409 `WORKLOG_SUBMIT_IN_PROGRESS` 不自動重試**，只說「正在送出中」並給重讀鈕。
 *    自動重試正是那道鎖要防的動作。
 * 3. **`not_attempted_count > 0` 要叫 Viewer 再按一次。** 停送的筆在 DB 裡與
 *    「從未送過」長得一樣，這句話是他唯一看得出「這批沒跑完」的地方。
 *
 * 送出前一定二次確認，確認框裡列出每一筆會送出的內容，並用全站唯一的
 * `text-metric-lg` 顯示「實際會送出幾小時」——那是不可逆操作前的最後一個數字。
 */
export function WorklogSubmitBar({
  day,
  blockReason,
  submitting,
  summary,
  error,
  onSubmit,
  onReload,
}: {
  day: WorklogDay
  /** 送出鈕為什麼不能按（`submitBlockReason`）；`null` ＝可以按 */
  blockReason: string | null
  submitting: boolean
  summary: WorklogSubmitSummary | null
  error: WorklogDayError | null
  onSubmit: () => void
  onReload: () => void
}) {
  const [confirmOpen, setConfirmOpen] = useState(false)
  const totals = daySubmitTotals(day)
  const pending = day.entries.filter((entry) => !isEntryLocked(entry))

  const preview = pending
    .map(
      (entry) =>
        `#${entry.seq}　${entry.issue_key ?? '（未選專案）'}　${formatHours(entry.hours)} h　${entry.note ?? ''}`,
    )
    .join('\n')

  return (
    <section aria-label="送出" className="border-line-strong border-t pt-3">
      <p className="text-fg-dim text-xs">
        待送出 <span className="metric text-foreground">{totals.pendingCount}</span> 筆，合計{' '}
        <span className="metric text-foreground">{formatHours(totals.pendingHours)}</span> h
      </p>
      {/* 不在前端先擋：送出閘的判準在後端（例如帶 entry_ids 時只看選中的筆），
          前端自己算一份遲早對不起來。這裡只預告，實際擋不擋以回應為準。 */}
      {totals.unassignedCount > 0 || totals.missingHoursCount > 0 ? (
        <p className="text-caution mt-1 text-xs leading-relaxed">
          {totals.unassignedCount > 0 ? `還有 ${totals.unassignedCount} 筆沒選專案。` : ''}
          {totals.missingHoursCount > 0 ? `還有 ${totals.missingHoursCount} 筆沒有時數。` : ''}
          有任何一筆送不出去，整批會一筆都不送。
        </p>
      ) : null}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button disabled={blockReason !== null} onClick={() => setConfirmOpen(true)}>
          {submitting ? '送出中…' : '送出到 ZPlanner'}
        </Button>
        {blockReason && !submitting ? <span className="text-fg-subtle text-xs">{blockReason}</span> : null}
        {submitting ? (
          <span role="status" className="text-fg-subtle text-xs">
            正在逐筆寫進 ZPlanner，可能要一兩分鐘。請不要重新整理或再按一次。
          </span>
        ) : null}
      </div>

      {error ? (
        <div role="alert" className="text-destructive mt-2 text-xs leading-relaxed">
          <p>{submitErrorText(error)}</p>
          {error.code === 'WORKLOG_SUBMIT_IN_PROGRESS' ? (
            <Button size="xs" variant="outline" className="mt-1.5" onClick={onReload}>
              重讀
            </Button>
          ) : null}
        </div>
      ) : null}

      {summary ? (
        // 第二行在 not_attempted_count > 0 時固定是「再按一次」那句（見 submitSummaryLines）
        <div role="status" aria-label="送出結果" className="mt-2 space-y-1 text-xs leading-relaxed">
          {submitSummaryLines(summary).map((line, index) => (
            <p
              key={index}
              className={summary.not_attempted_count > 0 && index === 1 ? 'text-caution' : 'text-fg-dim'}
            >
              {line}
            </p>
          ))}
        </div>
      ) : null}

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`把 ${day.work_date} 的工時送進 ZPlanner？`}
        description={
          <span className="block space-y-1">
            <span className="block">
              實際會送出 <span className="metric text-metric-lg text-foreground">{formatHours(totals.pendingHours)}</span> 小時、
              {totals.pendingCount} 筆。
            </span>
            <span className="block">
              送出後會以你的身分寫進 ZPlanner。月結之後要改只能走更正申請，送出前請確認每一筆的專案與時數。
            </span>
          </span>
        }
        preview={preview}
        previewLabel="會送出的每一筆"
        confirmLabel="確認送出"
        pending={submitting}
        onConfirm={() => {
          setConfirmOpen(false)
          onSubmit()
        }}
      />
    </section>
  )
}
