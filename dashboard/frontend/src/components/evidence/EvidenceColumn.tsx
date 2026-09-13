import { useMemo } from 'react'
import { EvidenceList } from '@/components/evidence/EvidenceList'
import { toEvidence, type EvidenceOrigin } from '@/lib/evidence'
import type { DraftPolishMeta, SseMeta } from '@/lib/types'
import { useDraftStore } from '@/store/draft'
import { providerLabel, useProviderStore } from '@/store/providers'
import { useSummaryStore } from '@/store/summary'

interface EvidenceColumnProps {
  origin: EvidenceOrigin
}

/** 一個來源要餵給 `toEvidence` 的全部東西。 */
interface EvidenceSource {
  meta: SseMeta | null
  polish: DraftPolishMeta | null
  streaming: boolean
  restored: boolean
}

/**
 * 還沒有證據時那句話。
 *
 * 三個來源各自說清楚「產生什麼之後這裡會長出東西」——寫成共用的一句
 * 「尚無資料」等於沒說，使用者不會知道要先做什麼才會有。
 */
const EMPTY_HINT: Record<EvidenceOrigin, string> = {
  summary: '產生 Summary 之後，這裡會列出它讀了哪些訊息、用了哪個模型。',
  draft:
    '產生 Draft Reply 之後，這裡會列出它建立在什麼之上——脈絡取了幾則、圖片讀進去幾張、程式碼命中哪些檔案、潤稿有沒有採用。',
  worklog:
    '產生工時草稿之後，這裡會列出它建立在什麼之上——掃了哪些 repo、抓到幾個 commit、Chat 上有幾則發言、ZPlanner 查到哪些可以填工時的 issue。',
}

/**
 * 證據清單與「有幾項需要看一眼」。
 *
 * 抽成 hook 是因為頂列的證據鈕也要 `attentionCount`（抽屜關著時得讓人知道
 * 值得打開），而重算一次 `toEvidence` 會讓兩邊有機會不一致。
 */
export function useEvidenceBundle(origin: EvidenceOrigin) {
  const providers = useProviderStore((state) => state.providers)

  const draftMeta = useDraftStore((state) => state.meta)
  const draftPolish = useDraftStore((state) => state.polish)
  const draftStreaming = useDraftStore((state) => state.streaming)
  const draftRestored = useDraftStore((state) => state.restored)

  const summaryMeta = useSummaryStore((state) => state.meta)
  const summaryStreaming = useSummaryStore((state) => state.streaming)

  return useMemo(() => {
    /**
     * 來源查表，不用 `isDraft` 這種二元旗標。
     *
     * 原本是 `const isDraft = origin === 'draft'` 再到處 `isDraft ? A : B`。
     * 二元的時候沒問題，加第三個來源的那一刻它會**安靜地把 worklog 餵成
     * summary 的資料**——證據欄照常渲染，只是內容是別的工作台的。
     *
     * `satisfies Record<EvidenceOrigin, …>` 讓「漏掉一個來源」變成編譯錯誤。
     */
    const sources = {
      summary: {
        meta: summaryMeta,
        polish: null,
        streaming: summaryStreaming,
        restored: false,
      },
      draft: {
        meta: draftMeta,
        polish: draftPolish,
        streaming: draftStreaming,
        // 還原的草稿缺的那幾列要說對理由（「沒有保存」而不是「伺服器沒回報」）
        restored: draftRestored,
      },
      // 階段 2a：工時草稿還沒有串流，證據欄一律是空的（會顯示 EMPTY_HINT）。
      // 2b 接上 SSE 之後，這裡換成 useWorklogStore 的 meta 與 streaming。
      worklog: { meta: null, polish: null, streaming: false, restored: false },
    } satisfies Record<EvidenceOrigin, EvidenceSource>

    const source = sources[origin]

    return {
      bundle: toEvidence({
        origin,
        meta: source.meta,
        polish: source.polish,
        streaming: source.streaming,
        providerLabel: (name: string) => providerLabel(providers, name),
        restored: source.restored,
      }),
      streaming: source.streaming,
    }
  }, [
    origin,
    draftMeta,
    draftPolish,
    draftStreaming,
    draftRestored,
    summaryMeta,
    summaryStreaming,
    providers,
  ])
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
  const { bundle, streaming } = useEvidenceBundle(origin)
  const empty = bundle.items.length === 0

  return (
    <section aria-labelledby="evidence-heading" className="bg-surface flex min-h-0 flex-col">
      {/* 標頭下用 --line-strong（結構線），不是語意線 --line-evidence——
          兩者是不同頻道（設計規格 §5.2 之 (d)） */}
      <div className="border-line-strong flex shrink-0 items-center gap-2 border-b px-gutter-tight py-2.5">
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
            {EMPTY_HINT[origin]}
          </p>
        ) : (
          <EvidenceList items={bundle.items} />
        )}
      </div>

      {!empty && !streaming ? (
        // 小計上同樣是結構線 --line-strong，不是語意線——見上方標頭的註解
        <div className="border-line-strong text-2xs flex shrink-0 justify-between border-t px-gutter-tight py-2">
          <span className="text-fg-dim">證據 {bundle.items.length} 項</span>
          <span className={bundle.attentionCount > 0 ? 'text-caution' : 'text-fg-dim'}>
            降級 {bundle.attentionCount} 項
          </span>
        </div>
      ) : null}
    </section>
  )
}
