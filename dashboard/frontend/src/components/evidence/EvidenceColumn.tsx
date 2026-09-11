import { useMemo } from 'react'
import { EvidenceList } from '@/components/evidence/EvidenceList'
import { toEvidence } from '@/lib/evidence'
import { useDraftStore } from '@/store/draft'
import { providerLabel, useProviderStore } from '@/store/providers'
import { useSummaryStore } from '@/store/summary'

interface EvidenceColumnProps {
  origin: 'summary' | 'draft'
}

/**
 * 證據清單與「有幾項需要看一眼」。
 *
 * 抽成 hook 是因為頂列的證據鈕也要 `attentionCount`（抽屜關著時得讓人知道
 * 值得打開），而重算一次 `toEvidence` 會讓兩邊有機會不一致。
 */
export function useEvidenceBundle(origin: 'summary' | 'draft') {
  const providers = useProviderStore((state) => state.providers)

  const draftMeta = useDraftStore((state) => state.meta)
  const draftPolish = useDraftStore((state) => state.polish)
  const draftStreaming = useDraftStore((state) => state.streaming)
  const draftRestored = useDraftStore((state) => state.restored)

  const summaryMeta = useSummaryStore((state) => state.meta)
  const summaryStreaming = useSummaryStore((state) => state.streaming)

  const isDraft = origin === 'draft'
  const streaming = isDraft ? draftStreaming : summaryStreaming

  return useMemo(
    () => ({
      bundle: toEvidence({
        origin,
        meta: isDraft ? draftMeta : summaryMeta,
        polish: isDraft ? draftPolish : null,
        streaming,
        providerLabel: (name: string) => providerLabel(providers, name),
        // 還原的草稿缺的那幾列要說對理由（「沒有保存」而不是「伺服器沒回報」）
        restored: isDraft && draftRestored,
      }),
      streaming,
      isDraft,
    }),
    [origin, isDraft, draftMeta, summaryMeta, draftPolish, streaming, providers, draftRestored],
  )
}

/**
 * 證據欄（設計規格 §5）。
 *
 * 這一欄回答一個問題：**這份產出建立在什麼之上。**
 *
 * 在此之前這些資訊被壓成一排會換行的彩色小藥丸（脈絡則數、合併幾則、圖片
 * 幾張、供應商、口氣、Persona、自訂提示、Sepia 狀態、每個 Reference Space），
 * 全部 11px、四色混雜、位置隨內容浮動——最該一眼看清的資訊變成最難掃的一塊。
 *
 * **不做進場動畫。** `meta` 在模型開口之前就到，所以這一欄第一秒就存在：
 * 有值的列立刻實體渲染，等值的列顯示閃動細豎條。欄位絕不淡入或 stagger——
 * 會淡入的數字是還不能相信的數字。
 */
export function EvidenceColumn({ origin }: EvidenceColumnProps) {
  const { bundle, streaming, isDraft } = useEvidenceBundle(origin)
  const empty = bundle.items.length === 0

  return (
    <section aria-labelledby="evidence-heading" className="flex min-h-0 flex-col">
      <div className="border-line flex shrink-0 items-center gap-2 border-b px-gutter-tight py-2.5">
        <h2 id="evidence-heading" className="text-xs font-semibold tracking-wide">
          證據
        </h2>
        {streaming ? (
          <span className="live-mark text-xs" role="status" aria-label="正在生成">
            ⟳
          </span>
        ) : null}
        {bundle.attentionCount > 0 ? (
          <span className="text-caution text-2xs ml-auto">
            † 需要看一眼 {bundle.attentionCount} 項
          </span>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto" aria-busy={streaming}>
        {empty ? (
          <p className="text-fg-dim px-gutter-tight py-6 text-xs leading-relaxed">
            {isDraft
              ? '產生 Draft Reply 之後，這裡會列出它建立在什麼之上——脈絡取了幾則、圖片讀進去幾張、程式碼命中哪些檔案、潤稿有沒有採用。'
              : '產生 Summary 之後，這裡會列出它讀了哪些訊息、用了哪個模型。'}
          </p>
        ) : (
          <EvidenceList items={bundle.items} />
        )}
      </div>

      {!empty && !streaming ? (
        <div className="border-line-evidence text-2xs flex shrink-0 justify-between border-t px-gutter-tight py-2">
          <span className="text-fg-dim">證據 {bundle.items.length} 項</span>
          <span className={bundle.attentionCount > 0 ? 'text-caution' : 'text-fg-dim'}>
            降級 {bundle.attentionCount} 項
          </span>
        </div>
      ) : null}
    </section>
  )
}
