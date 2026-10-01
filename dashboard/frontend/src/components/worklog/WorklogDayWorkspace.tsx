import { useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { WorklogDayEmpty } from '@/components/worklog/WorklogDayEmpty'
import { WorklogEntryCard } from '@/components/worklog/WorklogEntryCard'
import { WorklogHoursBar } from '@/components/worklog/WorklogHoursBar'
import { WorklogSubmitBar } from '@/components/worklog/WorklogSubmitBar'
import {
  clearedAttributionsText,
  submitBlockReason,
  useWorklogDayStore,
  type WorklogDayError,
  type WorklogStreamProgress,
} from '@/store/worklogDay'

/**
 * 工時草稿的單日工作區（中欄）。
 *
 * 由上而下：時數工具列 → 草稿卡清單 → 送出區。沿用 Draft Reply 的隱喻：
 * **系統產草稿、人逐筆確認、以本人身分送出**。狀態全在 `store/worklogDay.ts`，
 * 這裡只依 `phase` 決定畫哪一段（D-18）。
 *
 * ### ⚠️ 換日期要中止串流，但不能只靠卸載
 *
 * `Pane` 刻意常駐掛載（見 `AppShell`），切到別的工作台**不會**卸載這個元件，
 * 串流照跑——那是對的，切頁籤不該中止（AC-2）。換日期時元件也不卸載，只有
 * `date` prop 變了，所以中止靠 `open(date)`：換日就中止舊串流、重置、讀新的一天。
 * 卸載時另外呼叫 `abort()`。
 *
 * ### 這裡不會有的東西
 *
 * **不會有「自動選好單」的捷徑**，「上次記到」也只是選單最上面的一項。系統只給
 * 候選、信心度與理由，選擇權在 Viewer；也**不做批次套用**，每一段各自選——理由見 `docs/adr/0008-draft-worklog-over-clipboard-export.md`：
 * 工時會進月結，月結後要走 correction-request 才改得動，一個「多半正確」
 * 的自動歸屬，代價由一個月後發現錯誤的人支付。
 */
export function WorklogDayWorkspace({ date }: { date: string | null }) {
  const phase = useWorklogDayStore((s) => s.phase)
  const day = useWorklogDayStore((s) => s.day)
  const reloadError = useWorklogDayStore((s) => s.reloadError)
  const stream = useWorklogDayStore((s) => s.stream)
  const generateError = useWorklogDayStore((s) => s.generateError)
  const clearedAttributions = useWorklogDayStore((s) => s.clearedAttributions)
  const savingIds = useWorklogDayStore((s) => s.savingIds)
  const dirtyIds = useWorklogDayStore((s) => s.dirtyIds)
  const entryErrors = useWorklogDayStore((s) => s.entryErrors)
  const submitting = useWorklogDayStore((s) => s.submitting)
  const submitSummary = useWorklogDayStore((s) => s.submitSummary)
  const submitError = useWorklogDayStore((s) => s.submitError)

  const open = useWorklogDayStore((s) => s.open)
  const reload = useWorklogDayStore((s) => s.reload)
  const generate = useWorklogDayStore((s) => s.generate)
  const updateEntry = useWorklogDayStore((s) => s.updateEntry)
  const setDirty = useWorklogDayStore((s) => s.setDirty)
  const submit = useWorklogDayStore((s) => s.submit)

  /**
   * 🔴 `date === null` **不是**「換到沒有日期」，是「這次沒有指定哪一天」——
   * 一律忽略，不 abort、不重置。
   *
   * 為什麼不能當成換日期：`route.worklogDate` 只在 worklog section 才有值，切到
   * 摘要或收件匣時它就變成 null（`lib/route.ts` 的 `DEFAULT_ROUTE`），而這個元件
   * 常駐掛載、照樣收到這個 null。把它交給 `open(null)` 等於「切頁籤就中止串流、
   * 丟掉整份畫面」，違反 AC-2（2026-09-25 plan-verify 實測：延後 4 秒送出的串流，
   * 切到摘要 1249 ms 就被 abort）。回到 `#/worklog` 時網址會由 `useRouteSync`
   * 補回上次那一天，所以這裡只需要對「有值的日期」反應。
   * 真正要清掉狀態的時機（登出等）走 store 的 `reset()`，不經過這裡。
   */
  useEffect(() => {
    if (date === null) return
    open(date)
  }, [date, open])

  // 卸載時中止串流（前端注意事項第 1 點：否則串流會繼續跑）。
  // StrictMode 的「掛載→卸載→再掛載」會走到這裡一次：abort 會順便重讀這一天，
  // 所以不會卡在 streaming。
  useEffect(() => () => useWorklogDayStore.getState().abort(), [])

  const blockReason = submitBlockReason({ phase, day, savingIds, dirtyIds, submitting })
  const busy = submitting || savingIds.length > 0

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="px-gutter py-gutter-tight">
        <h1 className="text-lg font-semibold">
          工時草稿{date ? <span className="metric text-fg-dim ml-2 text-sm font-normal">{date}</span> : null}
        </h1>

        <div className="mt-3 space-y-4">
          {phase.kind === 'idle' ? (
            <p className="text-fg-dim max-w-prose text-sm leading-relaxed">
              從左欄挑一天開始。這裡會從你的 git commit 與 Chat 發言整理出那天做了什麼，你輸入當天總時數，
              系統按證據比例分配成 0.5 的倍數，你再逐筆確認每一段要記到哪張單。
            </p>
          ) : null}

          {phase.kind === 'loading' ? <p className="text-fg-dim text-sm">載入中…</p> : null}

          {phase.kind === 'load-failed' ? (
            // 🔴 不知道有沒有草稿時不畫「產生草稿」：那會引導 Viewer 去重產，
            // 而重產會把對不上新分段的已選單號清空
            <div role="alert" className="text-sm leading-relaxed">
              <p className="text-destructive">讀不到這一天的草稿：{phase.message}</p>
              <p className="text-fg-dim mt-1 text-xs">這不代表沒有草稿。先重讀一次再決定要不要產生。</p>
              <Button size="sm" variant="outline" className="mt-2" onClick={() => void reload()}>
                重讀
              </Button>
            </div>
          ) : null}

          {generateError ? <GenerateErrorNotice error={generateError} /> : null}

          {phase.kind === 'absent' && date ? (
            <WorklogDayEmpty date={date} onGenerate={(totalHours) => void generate({ totalHours })} />
          ) : null}

          {phase.kind === 'streaming' ? <StreamProgress date={date} stream={stream} /> : null}

          {phase.kind === 'ready' && day ? (
            <>
              {reloadError ? (
                // 有草稿時重讀失敗：保留舊的畫面，只標出可能不是最新
                <div role="alert" className="text-caution text-xs leading-relaxed">
                  <p>重讀失敗，下面可能不是最新的內容：{reloadError}</p>
                  <Button size="xs" variant="outline" className="mt-1.5" onClick={() => void reload()}>
                    重讀
                  </Button>
                </div>
              ) : null}

              {clearedAttributions ? (
                // Viewer 已決定：對不上新分段的單號照規格留空、不擋，事後在這裡列出。
                // 單號是資料，原樣列出，Viewer 才知道要回頭補選哪幾段
                <p role="status" data-notice="attributions-cleared" className="text-caution text-xs leading-relaxed">
                  {clearedAttributionsText(clearedAttributions)}。這幾段要重新選要記到哪張單。
                </p>
              ) : null}

              <WorklogHoursBar
                day={day}
                disabled={busy}
                onRegenerate={(totalHours) => void generate({ totalHours })}
              />

              {day.entries.length === 0 ? (
                <p className="text-fg-dim text-sm">這份草稿沒有任何一筆。</p>
              ) : (
                <ol aria-label="草稿卡" className="space-y-3">
                  {day.entries.map((entry) => (
                    <li key={entry.id}>
                      <WorklogEntryCard
                        entry={entry}
                        generationMeta={day.generation_meta}
                        saving={savingIds.includes(entry.id)}
                        error={entryErrors[entry.id] ?? null}
                        onSave={updateEntry}
                        onDirtyChange={setDirty}
                      />
                    </li>
                  ))}
                </ol>
              )}

              <WorklogSubmitBar
                day={day}
                blockReason={blockReason}
                submitting={submitting}
                summary={submitSummary}
                error={submitError}
                onSubmit={() => void submit()}
                onReload={() => void reload()}
              />
            </>
          ) : null}
        </div>
      </div>

    </div>
  )
}

/** 產生草稿失敗的說明。`NO_EVIDENCE` 要講出下一步，其他照後端的句子。 */
function GenerateErrorNotice({ error }: { error: WorklogDayError }) {
  return (
    <div role="alert" className="text-destructive text-sm leading-relaxed">
      <p>產生草稿失敗：{error.message}</p>
      {error.code === 'NO_EVIDENCE' ? (
        <p className="text-fg-dim mt-1 text-xs">
          這一天找不到任何 commit 或 Chat 發言，所以沒有建立草稿。確認設定中心登錄了工作 repo，或換一天再試。
        </p>
      ) : null}
      {error.code === 'WORKLOG_ALREADY_SUBMITTED' ? (
        <p className="text-fg-dim mt-1 text-xs">
          這一天已經有工時送進 ZPlanner，不能整份重新產生——那會弄丟 ZPlanner 那邊的工時編號。
        </p>
      ) : null}
    </div>
  )
}

/**
 * 串流進行中的畫面（AC-2）：meta 到了先講掃到什麼，每個 chunk 到了多一群。
 *
 * 這裡只畫進度，**不是草稿卡**：卡片一律等 done 之後讀 GET 再畫（D-10），
 * 串流中途的東西沒有 entry id、沒有存檔的地方，畫成卡片會讓人以為可以操作了。
 */
function StreamProgress({ date, stream }: { date: string | null; stream: WorklogStreamProgress }) {
  const { meta, chunks } = stream
  const labelOf = new Map((meta?.clusters ?? []).map((cluster) => [cluster.key, cluster.label]))

  return (
    <section role="status" aria-label="正在產生草稿" className="text-sm leading-relaxed">
      <p className="text-fg-dim">正在整理 {date ?? ''} 的證據…</p>
      {meta ? (
        <p className="text-fg-dim mt-1 text-xs">
          找到 <span className="metric">{meta.commits.length}</span> 個 commit、
          <span className="metric">{meta.chat.length}</span> 則發言，分成{' '}
          <span className="metric">{meta.clusters.length}</span> 群。
          {meta.zplanner && meta.zplanner.available === false
            ? `ZPlanner 目前不可用（${meta.zplanner.reason}），這次沒有系統候選的單。`
            : ''}
        </p>
      ) : null}
      {chunks.length > 0 ? (
        <ol className="mt-2 space-y-1 text-xs">
          {chunks.map((chunk) => (
            <li key={`${chunk.index}-${chunk.cluster_key}`} className="text-fg-dim">
              <span className="text-foreground font-medium">{labelOf.get(chunk.cluster_key) ?? chunk.cluster_key}</span>
              ：{chunk.text}
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  )
}
