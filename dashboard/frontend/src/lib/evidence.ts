import { formatDateTime } from '@/lib/format'
import type { DraftPolishMeta, SseMeta } from '@/lib/types'

/**
 * 證據欄的資料層（設計規格 §5）。
 *
 * 純函式、node 環境可測、**不 import 任何 store**。所有輸入從外面注入。
 *
 * 存在的理由：這些資訊現在散在 JSX 裡，其中四項只活在 `title` 屬性上——
 * 鍵盤與觸控使用者完全拿不到。把它們抽成有型別的資料，可測性與可及性
 * 一次解決。
 */

export type EvidenceKind =
  | 'context'
  | 'answering'
  | 'reference'
  | 'code'
  | 'images'
  | 'model'
  | 'reply-setting'
  | 'polish'
  | 'source'

/**
 * 可信度狀態。
 *
 * - `ok`：有值且完整
 * - `degraded`：有值但不完整（coverage=partial、命中 0 筆、潤稿未採用、圖片被略過）
 * - `pending`：還在跑
 * - `missing`：**後端沒回報這個欄位**（舊版後端）。不可畫成 ok——
 *   「沒有圖片」與「不知道有沒有讀圖片」是兩件事。
 */
export type EvidenceStatus = 'ok' | 'degraded' | 'pending' | 'missing'

/** 一行明細。取代 `title=`，一律畫成可見文字。 */
export interface EvidenceDetail {
  label?: string
  value?: string
  /** 多值：被略過的圖片、合併回覆的對象、命中的檔案 */
  values?: string[]
  /** 用 .metric 呈現（commit sha、分支、數字、時間） */
  metric?: boolean
}

export interface EvidenceItem {
  id: string
  kind: EvidenceKind
  status: EvidenceStatus
  /** 標籤欄的字 */
  label: string
  /** 右側計量值；沒有計量的列留空 */
  metric?: { value: string; unit?: string }
  /** 收合時那一行的補充（例：「討論串」）。不得是唯一資訊來源 */
  summary?: string
  detail: EvidenceDetail[]
  /** status !== 'ok' 時的「為什麼」，畫在明細最上面 */
  reason?: string
  /** 例：分支不存在 → 連到參考專案設定 */
  action?: { label: string; href: string }
  /** true 則不得收合——誤判的代價太高 */
  critical?: boolean
}

export interface EvidenceBundle {
  runId: string
  origin: 'summary' | 'draft'
  items: EvidenceItem[]
  /** 非 ok 的項數；抽屜關著時頂列亮點看這個 */
  attentionCount: number
}

const CONTEXT_MODE_LABEL: Record<string, string> = {
  flat_window: '前後脈絡',
  thread: '討論串',
  thread_thin: '討論串＋鄰近',
}

interface ToEvidenceInput {
  origin: 'summary' | 'draft'
  meta: SseMeta | null
  polish: DraftPolishMeta | null
  streaming: boolean
  /** 注入，evidence.ts 不 import store */
  providerLabel: (name: string) => string
}

function timeRange(start?: string, end?: string): string | null {
  if (!start || !end) return null
  return `${formatDateTime(start)} – ${formatDateTime(end)}`
}

/** 還在跑、而且這個欄位還沒有值時的佔位項。 */
function pendingItem(kind: EvidenceKind, label: string): EvidenceItem {
  return { id: kind, kind, status: 'pending', label, detail: [] }
}

function contextItem(meta: SseMeta): EvidenceItem {
  const context = meta.context
  if (!context) {
    // 舊版後端沒有這個欄位。**不可畫成 ok**——顯示「不知道」比顯示一個
    // 看起來完整的數字誠實得多。
    return {
      id: 'context',
      kind: 'context',
      status: 'missing',
      label: '脈絡',
      summary: '這個版本的伺服器沒有回報脈絡形狀',
      metric: meta.thread_message_count
        ? { value: String(meta.thread_message_count), unit: '則' }
        : undefined,
      detail: [],
    }
  }

  const partial = context.coverage === 'partial'
  const detail: EvidenceDetail[] = []
  const range = timeRange(context.time_range?.start, context.time_range?.end)
  if (range) detail.push({ value: range, metric: true })
  for (const block of context.blocks ?? []) {
    detail.push({ label: block.label, value: `${block.count} 則`, metric: true })
  }

  return {
    id: 'context',
    kind: 'context',
    status: partial ? 'degraded' : 'ok',
    label: '脈絡',
    summary: CONTEXT_MODE_LABEL[context.mode] ?? '脈絡',
    metric: { value: String(context.message_count), unit: '則' },
    reason: partial
      ? '系統沒能取回這則訊息周圍的完整對話（它可能太舊了），脈絡不保證連續'
      : undefined,
    // 不連續是「可能讓我不送」的第一順位，不准收起來
    critical: partial,
    detail,
  }
}

function answeringItem(meta: SseMeta): EvidenceItem | null {
  const answering = meta.answering ?? []
  // 只有真的合併（兩則以上）才需要這一列——單則回覆時它是廢話
  if (answering.length <= 1) return null
  return {
    id: 'answering',
    kind: 'answering',
    status: 'ok',
    label: '回覆對象',
    metric: { value: String(answering.length), unit: '則' },
    // 送出會一次結掉這幾則而且不可撤回，永遠展開
    critical: true,
    reason: `送出後這 ${answering.length} 則會一起標記為已處理`,
    detail: answering.map((a) => ({
      label: a.sender_display ?? '未知成員',
      value: a.create_time ? formatDateTime(a.create_time) : '',
      metric: true,
    })),
  }
}

function imagesItem(meta: SseMeta): EvidenceItem {
  if (meta.image_count === undefined) {
    return {
      id: 'images',
      kind: 'images',
      status: 'missing',
      label: '附件',
      summary: '這個版本的伺服器沒有回報圖片張數',
      detail: [],
    }
  }
  const skipped = meta.images_skipped ?? []
  return {
    id: 'images',
    kind: 'images',
    status: skipped.length > 0 ? 'degraded' : 'ok',
    label: '附件',
    metric: { value: String(meta.image_count), unit: '張' },
    reason: skipped.length > 0 ? `有 ${skipped.length} 張沒有送進模型` : undefined,
    detail: skipped.length > 0 ? [{ label: '略過', values: skipped }] : [],
  }
}

function referenceItem(meta: SseMeta): EvidenceItem | null {
  const refs = meta.reference_spaces ?? []
  if (refs.length === 0) return null
  return {
    id: 'reference',
    kind: 'reference',
    status: 'ok',
    label: '參考 Space',
    metric: { value: String(refs.length), unit: '個' },
    detail: refs.map((ref) => ({
      label: ref.space_name,
      value: `${ref.message_count} 則`,
      metric: true,
    })),
  }
}

function codeItems(meta: SseMeta): EvidenceItem[] {
  const refs = meta.code_refs ?? []
  const skipped = meta.code_skipped ?? []
  if (refs.length === 0 && skipped.length === 0) return []

  const items: EvidenceItem[] = refs.map((ref, index) => {
    const detail: EvidenceDetail[] = [
      { value: `${ref.branch}@${ref.commit_sha}`, metric: true },
    ]
    if (ref.commit_date) detail.push({ label: 'commit', value: ref.commit_date.slice(0, 10), metric: true })
    if (ref.terms.length) detail.push({ label: '關鍵字', values: ref.terms })
    if (ref.hit_count > 0) {
      detail.push({ label: '命中', values: ref.files })
    }
    for (const note of ref.notes ?? []) detail.push({ value: `※ ${note}` })

    const empty = ref.hit_count === 0
    return {
      id: `code-${ref.project_name}-${ref.environment}-${index}`,
      kind: 'code',
      status: empty ? 'degraded' : 'ok',
      label: index === 0 ? '參考專案' : '',
      summary: `${ref.project_name}　${ref.environment_label}`,
      metric: empty ? undefined : { value: String(ref.hit_count), unit: '檔' },
      reason: empty ? '這個分支沒有找到相符的程式碼' : undefined,
      // 搜錯環境、搜錯分支正是這個功能最貴的失敗，直接給一條路過去改
      action: { label: '檢查分支設定', href: '#/settings/code-projects' },
      detail,
    }
  })

  if (skipped.length > 0) {
    items.push({
      id: 'code-skipped',
      kind: 'code',
      status: 'degraded',
      label: items.length ? '' : '參考專案',
      summary: '有專案被略過',
      reason: '這些勾選的專案沒有進到檢索',
      action: { label: '檢查分支設定', href: '#/settings/code-projects' },
      detail: [{ values: skipped }],
    })
  }
  return items
}

function modelItem(meta: SseMeta, providerLabel: (name: string) => string): EvidenceItem {
  if (!meta.provider) return pendingItem('model', '生成')
  return {
    id: 'model',
    kind: 'model',
    status: 'ok',
    label: '生成',
    summary: providerLabel(meta.provider),
    detail: meta.model ? [{ value: meta.model, metric: true }] : [],
  }
}

function replySettingItem(meta: SseMeta): EvidenceItem | null {
  const reply = meta.reply
  if (!reply) return null
  const detail: EvidenceDetail[] = []
  if (reply.tone_label) detail.push({ label: '口氣', value: reply.tone_label })
  detail.push({ label: 'Persona', value: reply.persona_name ?? '未使用' })
  if (reply.custom_prompt) detail.push({ label: '自訂提示', value: '已套用' })
  return {
    id: 'reply-setting',
    kind: 'reply-setting',
    status: 'ok',
    label: '回話設定',
    detail,
  }
}

function polishItem(meta: SseMeta, polish: DraftPolishMeta | null, streaming: boolean): EvidenceItem | null {
  const wanted = meta.reply?.sepia === true
  if (!wanted && !polish) return null

  if (!polish) {
    // 潤稿發生在串流結束之後，所以這中間會有一段「跑了但還沒有結果」
    return streaming
      ? pendingItem('polish', '潤稿')
      : { id: 'polish', kind: 'polish', status: 'pending', label: '潤稿', summary: 'Sepia 潤稿中', detail: [] }
  }

  return {
    id: 'polish',
    kind: 'polish',
    status: polish.polished ? 'ok' : 'degraded',
    label: '潤稿',
    summary: polish.polished ? 'Sepia 已核對' : 'Sepia 未採用',
    // 使用者需要知道「是哪個事實被改動了」，那是判斷「模型在亂改」還是
    // 「檢查太嚴」的唯一依據
    reason: polish.polished ? undefined : (polish.fallback_reason ?? '潤稿未採用，顯示的是未潤稿的版本'),
    critical: !polish.polished,
    detail: polish.polish_model ? [{ label: '模型', value: polish.polish_model, metric: true }] : [],
  }
}

function sourceItem(meta: SseMeta): EvidenceItem {
  return {
    id: 'source',
    kind: 'source',
    status: 'ok',
    label: '來源',
    summary: meta.space ?? '',
    metric:
      meta.message_count !== undefined
        ? { value: String(meta.message_count), unit: '則' }
        : undefined,
    detail: meta.space_id ? [{ value: meta.space_id, metric: true }] : [],
  }
}

/**
 * 把 SSE meta 正規化成證據清單。
 *
 * 欄位順序＝「**最可能讓我不送**」優先。這個順序是固定的，不隨內容變動——
 * 使用者掃證據欄的動線每次都一樣，才掃得快。
 */
export function toEvidence(input: ToEvidenceInput): EvidenceBundle {
  const { origin, meta, polish, streaming, providerLabel } = input

  if (!meta) {
    return {
      runId: 'pending',
      origin,
      items: streaming ? [pendingItem('source', origin === 'draft' ? '脈絡' : '來源')] : [],
      attentionCount: 0,
    }
  }

  const items: EvidenceItem[] = []

  if (origin === 'draft') {
    items.push(contextItem(meta))
    const answering = answeringItem(meta)
    if (answering) items.push(answering)
    items.push(imagesItem(meta))
    const reference = referenceItem(meta)
    if (reference) items.push(reference)
    items.push(...codeItems(meta))
    items.push(modelItem(meta, providerLabel))
    const replySetting = replySettingItem(meta)
    if (replySetting) items.push(replySetting)
    const polishRow = polishItem(meta, polish, streaming)
    if (polishRow) items.push(polishRow)
  } else {
    items.push(sourceItem(meta))
    items.push(imagesItem(meta))
    items.push(modelItem(meta, providerLabel))
  }

  return {
    runId: `${origin}-${meta.mention_id ?? meta.space_id ?? 'run'}`,
    origin,
    items,
    attentionCount: items.filter((item) => item.status !== 'ok').length,
  }
}
