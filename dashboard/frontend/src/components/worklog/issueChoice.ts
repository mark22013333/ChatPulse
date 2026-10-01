import type { WorklogCandidate, WorklogDayEntry, WorklogEntryUpdate, WorklogIssueSearchIssue } from '@/lib/types'

/**
 * 「記到哪張單」的純函式（沒有 React 也測得了）。
 *
 * ### 為什麼不放 `store/worklogDay.ts`
 *
 * 那裡管的是「這一天」的狀態機；這裡只是「一張卡的選單長什麼樣」的換算，
 * 只有 `WorklogIssuePicker` 與 `WorklogEntryCard` 用。放進 store 會讓 store
 * 開始知道選單的分組與文案。
 */

/** 歸屬四欄。**一律一起給**（D-12）：只寫一半的話，送出閘與重產守門對「指派了沒」答案不同。 */
export type WorklogAssignment = Pick<WorklogEntryUpdate, 'issue_id' | 'issue_key' | 'project_id' | 'project_key'>

export const CLEARED_ASSIGNMENT: WorklogAssignment = {
  issue_id: null,
  issue_key: null,
  project_id: null,
  project_key: null,
}

/** 候選或搜尋結果 → 要 PUT 的四欄。 */
export function toAssignment(issue: Pick<WorklogIssueSearchIssue, 'issue_id' | 'issue_key' | 'project_id' | 'project_key'>): WorklogAssignment {
  return {
    issue_id: issue.issue_id,
    issue_key: issue.issue_key,
    project_id: issue.project_id ?? null,
    project_key: issue.project_key ?? null,
  }
}

/**
 * 把候選拆成「上次記到」與其餘。
 *
 * 🔴 「上次記到」只是排在最上面的一個選項，**不是預選**——四欄是不是 null 只看
 * entry 本身（ADR-0008）。後端保證最多一筆，多出來的照順序當一般候選，不丟。
 */
export function splitCandidates(candidates: WorklogCandidate[]): {
  lastUsed: WorklogCandidate | null
  others: WorklogCandidate[]
} {
  const index = candidates.findIndex((c) => c.source === 'last_used')
  if (index === -1) return { lastUsed: null, others: candidates }
  return { lastUsed: candidates[index], others: [...candidates.slice(0, index), ...candidates.slice(index + 1)] }
}

/**
 * 搜尋框送出前的關鍵字。空白回 `null`＝**不要送**。
 *
 * ⚠️ ZPlanner 對空字串回 0 筆而不是全部：送出去畫面就會說「沒有可記工時的單」，
 * 錯得很合理。後端與 `api.searchWorklogIssues` 都會擋，這裡是第一道——擋在這裡
 * 才不會為了一個空字串閃一次「搜尋中…」。
 */
export function normalizeIssueQuery(input: string): string | null {
  const text = input.trim()
  return text === '' ? null : text
}

/**
 * 這張卡的候選是怎麼來的——三種「沒有候選」的原因要分開講，因為 Viewer 下一步不同：
 *
 * - `unavailable`：ZPlanner 沒設定或建不起 client。搜尋框停用，講原因（要找管理者）。
 * - `query-failed`：這一段的那次查詢失敗了。給重試（多半是暫時的）。
 * - `no-match`：有查、沒命中。請 Viewer 自己輸入搜尋（關鍵字是系統猜的，常常不準）。
 * - `has-candidates`：有系統候選。
 * - `unknown`：認不出 `generation_meta.zplanner` 的形狀（例如更早的草稿），**不猜**
 *   是哪一種，不顯示任何說法，搜尋框照常可用。
 *
 * 以前三種共用同一句「ZPlanner 不可用或沒有相符的單」，Viewer 無從判斷要等、
 * 要重試、還是要自己搜。
 *
 * 「上次記到」本身不算系統候選：它是本地記憶，與 ZPlanner 查詢有沒有命中無關。
 * 例外是它帶著 `matched_source`——同一張單這次也被搜到、兩筆已在後端合併，那就是命中。
 */
export type IssueLookupStatus =
  | { kind: 'unavailable'; reason: string }
  | { kind: 'query-failed'; reason: string; term: string | null }
  | { kind: 'no-match' }
  | { kind: 'has-candidates' }
  | { kind: 'unknown' }

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

export function issueLookupStatus(
  generationMeta: unknown,
  clusterKey: string | null,
  candidates: WorklogCandidate[],
): IssueLookupStatus {
  const zplanner = isRecord(generationMeta) ? generationMeta.zplanner : undefined
  if (!isRecord(zplanner) || typeof zplanner.available !== 'boolean') return { kind: 'unknown' }
  if (zplanner.available === false) {
    return { kind: 'unavailable', reason: typeof zplanner.reason === 'string' ? zplanner.reason : '' }
  }
  if (clusterKey !== null && Array.isArray(zplanner.errors)) {
    const failed = zplanner.errors.find((e) => isRecord(e) && e.cluster === clusterKey)
    if (isRecord(failed)) {
      return {
        kind: 'query-failed',
        reason: typeof failed.reason === 'string' ? failed.reason : '',
        term: typeof failed.term === 'string' && failed.term.trim() !== '' ? failed.term : null,
      }
    }
  }
  const { lastUsed, others } = splitCandidates(candidates)
  const hit = others.length > 0 || Boolean(lastUsed?.matched_source)
  return hit ? { kind: 'has-candidates' } : { kind: 'no-match' }
}

/** entry 目前的歸屬顯示成什麼。`null`＝未指派。 */
export function currentAssignmentLabel(entry: Pick<WorklogDayEntry, 'issue_id' | 'issue_key'>): string | null {
  if (entry.issue_id === null) return null
  return entry.issue_key ?? `#${entry.issue_id}`
}
