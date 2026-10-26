import { useMemo } from 'react'
import { AlertCircleIcon, CompassIcon } from 'lucide-react'
import { Markdown } from '@/components/Markdown'
import { DraftReplyEditor } from '@/components/draft/DraftReplyEditor'
import { formatDateTime } from '@/lib/format'
import { useAuthStore } from '@/store/auth'
import { splitDraft, useDraftStore } from '@/store/draft'

interface DraftOutputPaneProps {
  /** 這個工作台目前看得見嗎。看不見時讓 Markdown 停止重新 parse（規格 §7.3） */
  active: boolean
  onRequestSend: () => void
}

/**
 * 草稿右欄：脈絡分析 ＋ 建議回話（規格 §9.2）。
 *
 * 改版前這裡還有一排會換行的彩色小藥丸——脈絡則數、合併幾則、圖片幾張、
 * 供應商、口氣、Persona、自訂提示、Sepia 狀態、每個 Reference Space，
 * 全部 11px、四色混雜、位置隨內容浮動。它們現在住在右側的證據欄，有固定
 * 的位置與順序（規格 §5）。**不要把它們加回來。**
 */
export function DraftOutputPane({ active, onRequestSend }: DraftOutputPaneProps) {
  const scope = useDraftStore((s) => s.meta?.project_scope)
  const status = useDraftStore((s) => s.projectStatus)
  const evidenceEnabled = useAuthStore((s) => s.me?.features?.reply_evidence === true)
  const selectedProject = useDraftStore((s) => Boolean(s.active?.projectChoice))
  const evidenceMode = evidenceEnabled && (Boolean(scope) || selectedProject)
  const assessment = useDraftStore((s) => s.assessment)
  const receiptId = useDraftStore((s) => s.receiptId)
  const reviewPending = useDraftStore((s) => s.reviewPending)
  const reviewProgress = useDraftStore((s) => s.reviewProgress)
  const references = useDraftStore((s) => s.meta?.reference_spaces)
  const codeRefs = useDraftStore((s) => s.meta?.code_refs)
  const raw = useDraftStore((s) => s.raw)
  const streaming = useDraftStore((s) => s.streaming)
  const error = useDraftStore((s) => s.error)
  const polish = useDraftStore((s) => s.polish)
  const restored = useDraftStore((s) => s.restored)
  const restoredAt = useDraftStore((s) => s.restoredAt)
  // 還原的這份有沒有帶到完整證據（新格式才有）。用 context 當代表：
  // 它與 reference_spaces／answering／image_count 是同一批、同時存或同時缺。
  const hasFullEvidence = useDraftStore((s) => Boolean(s.meta?.context))

  const sections = useMemo(() => splitDraft(raw), [raw])

  return (
    <div className="min-h-0 overflow-y-auto p-5">
      {scope ? <section aria-label="本次專案範圍" className="mb-4 space-y-1 rounded border p-3 text-xs">
        <p className="font-semibold">{scope.profile_name} · r{scope.revision} · {evidenceEnabled ? '模型輔助評估，仍需確認' : '未語意查證'}</p>
        <p>{status === 'stale' ? '來源設定已失效，請重新產生' : status === 'complete' ? '生成完整，仍須人工確認' : '部分草稿，不可送出'}</p>
        <p className="break-all">目標 {scope.target_space_id} / {scope.target_thread_name ?? '無討論串'}</p>
        <p>本次選用 {scope.sources.filter((s) => s.use === 'reply_context').map((s) => s.label).join('、') || '無補充來源'}</p>
        {evidenceEnabled ? <p>僅供私有分析 {scope.sources.filter((s) => s.use === 'analysis_only').map((s) => s.label).join('、') || '無'}</p> : null}
        <p>僅顯示來源 {scope.sources.filter((s) => s.use === 'metadata_only').map((s) => s.label).join('、') || '無'}</p>
        {evidenceEnabled ? <p>本題環境 {scope.environment === 'production' ? '正式環境' : scope.environment === 'uat' ? 'UAT' : scope.environment === 'dev' ? '開發環境' : '未知'}</p> : null}
        <p>已回報實讀 {references?.map((s) => s.space_name).join('、') || '無 Space 回報'}；{codeRefs?.map((s) => s.project_name).join('、') || '無 Git 回報'}。空結果或部分查詢不代表完整支持。</p>
      </section> : null}
      {evidenceMode && reviewProgress ? <p role="status" className="mb-3 text-xs text-muted-foreground">{({ reading: '讀取已選來源', assessing: '整理證據', drafting: '撰寫回話', reviewing: '評估回話' })[reviewProgress]}…</p> : null}
      {evidenceMode && (assessment || reviewPending) ? <section aria-label="回話評估" className="mb-4 space-y-2 rounded border p-3 text-xs">
        <h3 className="font-semibold">模型輔助評估，仍需確認</h3>
        <p>{reviewPending ? '正在重新查核目前文字。' : receiptId ? '可進入送出確認；請逐項閱讀後再送出。' : '尚無可送出的查核結果，請查核目前回話或重新產生。'}</p>
        {assessment?.stop_reason ? <p>查核停止：{assessment.stop_reason}</p> : null}
        {assessment?.claims.map((claim) => <div key={claim.id} className="space-y-1 border-t pt-2">
          <p>{claim.text}</p>
          <p className="text-muted-foreground">{claim.relation === 'supported' ? '來源支持' : claim.relation === 'contradicted' ? '來源矛盾' : '支持不足'} · {claim.reference_validity === 'valid' ? '引用有效' : claim.reference_validity === 'invalid' ? '引用無效' : '引用待確認'} · {claim.currency === 'current' ? '來源為目前版本' : claim.currency === 'superseded' ? '來源已有新版' : '時效未知'} · {claim.scope === 'match' ? '範圍相符' : claim.scope === 'mismatch' ? '範圍不符' : '範圍未知'}</p>
          <p>{claim.reason}</p>
          {claim.citations.map((citation) => <p key={`${citation.evidence_id}-${citation.locator}`} className="break-all text-muted-foreground">引用：{citation.locator} · {citation.revision}</p>)}
        </div>)}
        {assessment?.gaps.length ? <div className="border-t pt-2"><p className="font-medium">仍有缺口</p><ul className="list-disc pl-4">{assessment.gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul></div> : null}
      </section> : null}
      {error ? (
        <div className="mb-4 flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
          <AlertCircleIcon className="mt-0.5 size-3.5 shrink-0" />
          <span className="leading-relaxed">{error}</span>
        </div>
      ) : null}

      {!raw && !streaming && !error ? (
        <p className="py-10 text-center text-xs text-muted-foreground">
          {evidenceMode ? '選好專案來源與本題目標後，按「產生 Draft Reply」。' : '勾好 Reference Space 之後按「產生 Draft Reply」。'}草稿永遠只是草稿，一定要你看過、改過、確認後才會送出。
        </p>
      ) : null}

      {raw || streaming || (evidenceMode && assessment) ? (
        <div className="space-y-4">
          {/*
            還原的草稿要**在內容上方**講清楚它是舊的。
            不講的話它與剛產生的長得一模一樣，而兩者差很多：這一份可能是
            好幾天前、用當時的設定與當時的對話內容產的，直接送出去會送出
            過期的回覆。放在內容上方而不是證據欄，是因為看內容的人不一定
            會展開證據欄。
          */}
          {restored ? (
            <div className="rounded border border-line-evidence bg-muted/40 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
              <span className="font-medium text-foreground">這是先前存下來的草稿</span>
              {restoredAt ? <span className="ml-1">產生於 {formatDateTime(restoredAt)}</span> : null}
              {/*
                只有**真的缺**證據時才這樣講。2026-09-11 起產生的草稿會把整份
                meta 一起存下來，證據欄是完整的——對那些還說「沒有保存」就是
                一句假話，而且會讓人白白重新產生一次（燒配額）。
                判準用 `meta.context` 在不在，因為那正是舊格式唯一缺的那一批
                欄位的代表（見 `store/draft.ts` 的 loadStored）。
              */}
              {hasFullEvidence ? (
                <span className="ml-1">證據欄是產生當下記錄的完整內容。</span>
              ) : (
                <span className="ml-1">
                  產生當下的脈絡證據沒有保存，證據欄只看得到生成、回話設定與潤稿。
                  要拿到完整證據請重新產生。
                </span>
              )}
            </div>
          ) : null}

          {/*
            潤稿被退回時要明說原因。只放一個琥珀 badge 不夠——
            使用者需要知道「是哪個事實被改動了」，那是判斷「模型在亂改」
            還是「檢查太嚴」的唯一依據。
          */}
          {polish && !polish.polished && polish.fallback_reason ? (
            <div className="mt-2 rounded border border-caution-line bg-caution/10 px-3 py-2 text-xs leading-relaxed text-caution">
              <span className="font-medium">Sepia 潤稿未採用</span>
              <span className="ml-1">{polish.fallback_reason}</span>
              <span className="ml-1 text-muted-foreground">
                {evidenceMode ? '下面顯示的是未潤稿的版本，請先查核再送出。' : '下面顯示的是未潤稿的版本，內容仍然可以直接送出。'}
              </span>
            </div>
          ) : null}

          {/*
            標題**在標頭帶裡**，不是浮在框外。

            改版前這裡是「一行 h3 ＋ 下面一個框」，兩者之間沒有從屬關係，
            與摘要工作台的產出卡也對不起來。收進標頭帶之後，這一頁有幾個
            區塊用掃的就數得出來——那正是卡片語法存在的理由。
          */}
          {!evidenceMode ? <section className="overflow-hidden rounded-xl border border-border bg-surface">
            <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2">
              <h3 className="flex items-center gap-1.5 text-sm font-semibold">
                <CompassIcon className="size-4 text-signal" />
                脈絡分析
              </h3>
            </div>
            {/* 卡片外框已經負責邊框與底色，內容只要負責內距（Markdown 類用 p-5）。
                原本的 `bg-card/60` 是階層落差還只有 0.013 時的補償手法。 */}
            <Markdown
              source={sections.context}
              typing={streaming && !sections.replyStarted}
              active={active}
              className="p-5"
            />
          </section> : null}

          <DraftReplyEditor onRequestSend={onRequestSend} />
        </div>
      ) : null}
    </div>
  )
}
