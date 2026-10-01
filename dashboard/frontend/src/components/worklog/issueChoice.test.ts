import { describe, expect, it } from 'vitest'
import {
  currentAssignmentLabel,
  issueLookupStatus,
  normalizeIssueQuery,
  splitCandidates,
  toAssignment,
} from '@/components/worklog/issueChoice'
import type { WorklogCandidate } from '@/lib/types'

/**
 * 「記到哪張單」的純函式。卡片的行為在 WorklogEntryCard.test.tsx 驗；這裡守的是
 * 換算規則本身，尤其是「認不出來就不猜」的那幾條。🔴 不斷言文案。
 */

function candidate(over: Partial<WorklogCandidate> = {}): WorklogCandidate {
  return {
    issue_id: 501,
    issue_key: 'PEI-133',
    title: '匯出逾時',
    project_id: 42,
    project_key: 'PEI',
    source: 'search',
    confidence: 'weak',
    reason: '關鍵字搜到的',
    can_worklog: null,
    permission_checked: false,
    permission_reason: '',
    ...over,
  }
}

describe('normalizeIssueQuery', () => {
  it('🔴 空白一律回 null（不要送出：ZPlanner 對空字串回 0 筆而不是全部）', () => {
    for (const value of ['', ' ', '\t\n', '　'.trim()]) expect(normalizeIssueQuery(value)).toBeNull()
  })

  it('正對照：有字就去掉前後空白後回傳', () => {
    expect(normalizeIssueQuery('  PEI-133 ')).toBe('PEI-133')
  })
})

describe('splitCandidates', () => {
  it('把「上次記到」拆出來，其餘照原順序', () => {
    const memory = candidate({ issue_id: 9, source: 'last_used' })
    const a = candidate({ issue_id: 1 })
    const b = candidate({ issue_id: 2 })
    expect(splitCandidates([a, memory, b])).toEqual({ lastUsed: memory, others: [a, b] })
  })

  it('沒有記憶時 lastUsed 是 null', () => {
    expect(splitCandidates([candidate()]).lastUsed).toBeNull()
  })
})

describe('currentAssignmentLabel', () => {
  it('🔴 issue_id 是 null 就是未指派（null），不看任何候選', () => {
    expect(currentAssignmentLabel({ issue_id: null, issue_key: null })).toBeNull()
  })

  it('有 id 沒有 key 時照實顯示 #id，不假裝成未指派', () => {
    expect(currentAssignmentLabel({ issue_id: 77, issue_key: null })).toBe('#77')
    expect(currentAssignmentLabel({ issue_id: 77, issue_key: 'OPS-7' })).toBe('OPS-7')
  })
})

describe('toAssignment', () => {
  it('四欄一起給，缺的 project 欄位是 null 不是 undefined（D-12）', () => {
    expect(toAssignment({ issue_id: 3, issue_key: 'A-3', project_id: null, project_key: null })).toEqual({
      issue_id: 3,
      issue_key: 'A-3',
      project_id: null,
      project_key: null,
    })
  })
})

describe('issueLookupStatus：三種「沒有候選」分開', () => {
  const meta = (zplanner: unknown) => ({ zplanner })

  it('available=false → unavailable，帶原因', () => {
    expect(issueLookupStatus(meta({ available: false, reason: '未設定' }), 'c-a', [])).toEqual({
      kind: 'unavailable',
      reason: '未設定',
    })
  })

  it('這一段在 errors[] 裡 → query-failed，帶關鍵字', () => {
    const status = issueLookupStatus(
      meta({ available: true, errors: [{ cluster: 'c-a', term: '匯出', reason: '逾時' }] }),
      'c-a',
      [],
    )
    expect(status).toEqual({ kind: 'query-failed', reason: '逾時', term: '匯出' })
  })

  it('有查沒命中 → no-match；只有「上次記到」也算沒命中', () => {
    expect(issueLookupStatus(meta({ available: true, errors: [] }), 'c-a', []).kind).toBe('no-match')
    expect(
      issueLookupStatus(meta({ available: true, errors: [] }), 'c-a', [candidate({ source: 'last_used' })]).kind,
    ).toBe('no-match')
  })

  it('「上次記到」與搜到的單合併過（matched_source 有值）→ 算命中', () => {
    expect(
      issueLookupStatus(meta({ available: true, errors: [] }), 'c-a', [
        candidate({ source: 'last_used', matched_source: 'issue_key', confidence: 'strong' }),
      ]).kind,
    ).toBe('has-candidates')
  })

  it('有系統候選 → has-candidates', () => {
    expect(issueLookupStatus(meta({ available: true, errors: [] }), 'c-a', [candidate()]).kind).toBe('has-candidates')
  })

  it('認不出 zplanner 段的形狀 → unknown，不猜是哪一種', () => {
    expect(issueLookupStatus(null, 'c-a', []).kind).toBe('unknown')
    expect(issueLookupStatus({}, 'c-a', []).kind).toBe('unknown')
    expect(issueLookupStatus(meta({ available: 'yes' }), 'c-a', []).kind).toBe('unknown')
  })

  it('對不到分群（cluster_key 為 null）時不認領任何一段的查詢失敗', () => {
    const status = issueLookupStatus(
      meta({ available: true, errors: [{ cluster: 'c-a', term: 'x', reason: '逾時' }] }),
      null,
      [],
    )
    expect(status.kind).toBe('no-match')
  })
})
