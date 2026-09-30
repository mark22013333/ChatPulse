import { describe, expect, it } from 'vitest'
import { paginate } from '@/lib/paginate'
import {
  UNASSIGNED_LABEL,
  assignmentLabel,
  assignmentOf,
  hoursRatio,
  isUnsent,
  maxDraftHours,
  parseWorklogStatusFilter,
  parseWorklogTab,
  sortWorklogRows,
  activeWorklogTab,
  unsentWeeks,
  worklogTableFilter,
} from '@/lib/worklogTable'
import { weekDayStatus } from '@/store/worklog'
import type { WorklogWeekDay, WorklogWeekDraft } from '@/lib/types'

/**
 * 工時草稿表格的純函式（2026-10-01 改版 §4.2、§4.4）。
 *
 * 最要緊的兩組：
 * 1. 專案分配欄**不讀系統建議**（ADR-0008）——用「資料裡確實帶著候選專案」當正對照。
 * 2. 排序對全部列做完再切頁——用「本來排在第二頁」的那一天當正對照。
 */

function draft(over: Partial<WorklogWeekDraft> = {}): WorklogWeekDraft {
  return {
    id: 1,
    work_date: '2026-09-07',
    status: 'draft',
    total_hours: '8.00',
    tz_offset: '+08:00',
    entry_count: 3,
    hours: '8.00',
    allocated_hours: '8.00',
    submitted_count: 0,
    failed_count: 0,
    unknown_count: 0,
    unassigned_count: 3,
    ...over,
  }
}

function day(work_date: string, d: WorklogWeekDraft | null = null, weekday = 1): WorklogWeekDay {
  return { work_date, weekday, draft: d, zplanner: null }
}

describe('tabs', () => {
  it('未送出＝上週＋本週兩週（API 一次只給一週）', () => {
    expect(unsentWeeks('2026-09-07', '2026-08-31')).toEqual(['2026-08-31', '2026-09-07'])
  })

  it('tab 是週次的捷徑：亮哪一顆由週次反推，翻到更早的週時一顆都不亮', () => {
    expect(activeWorklogTab('week', '2026-09-07', '2026-09-07', '2026-08-31')).toBe('this')
    expect(activeWorklogTab('week', '2026-08-31', '2026-09-07', '2026-08-31')).toBe('last')
    expect(activeWorklogTab('week', '2026-08-17', '2026-09-07', '2026-08-31')).toBeNull()
    expect(activeWorklogTab('unsent', '2026-09-07', '2026-09-07', '2026-08-31')).toBe('unsent')
  })

  it('認不得的 tab／狀態值回 null（Base UI 清除選擇時給 null）', () => {
    expect(parseWorklogTab(null)).toBeNull()
    expect(parseWorklogTab('unsent')).toBe('unsent')
    expect(parseWorklogStatusFilter(null)).toBeNull()
    expect(parseWorklogStatusFilter('partial')).toBe('partial')
  })

  it('未送出＝有草稿但沒整天送完；不含未產生與已送出', () => {
    expect(isUnsent('drafted')).toBe(true)
    expect(isUnsent('partial')).toBe(true)
    expect(isUnsent('absent')).toBe(false)
    expect(isUnsent('submitted')).toBe(false)
  })
})

describe('篩選', () => {
  const rows = [
    day('2026-09-07', draft({ status: 'submitted', unassigned_count: 0 })),
    day('2026-09-08', draft()),
    // 狀態寫 submitted、但有一筆逾時 → partial（weekDayStatus 的判準），要留在「未送出」
    day('2026-09-09', draft({ status: 'submitted', unknown_count: 1, unassigned_count: 0 })),
    day('2026-09-10'),
  ]

  it('未送出 tab 只留草稿與部分未完成', () => {
    const f = worklogTableFilter({ mode: 'unsent', status: 'all' }, weekDayStatus)
    expect(rows.filter(f).map((r) => r.work_date)).toEqual(['2026-09-08', '2026-09-09'])
  })

  it('狀態篩選與 tab 疊加', () => {
    const f = worklogTableFilter({ mode: 'week', status: 'absent' }, weekDayStatus)
    expect(rows.filter(f).map((r) => r.work_date)).toEqual(['2026-09-10'])
    const g = worklogTableFilter({ mode: 'unsent', status: 'partial' }, weekDayStatus)
    expect(rows.filter(g).map((r) => r.work_date)).toEqual(['2026-09-09'])
  })
})

describe('排序對全部列做完再切頁', () => {
  /** 14 天，時數最多的是**最後一天**——依日期排、每頁 10 筆時它在第二頁。 */
  const fourteen = Array.from({ length: 14 }, (_, i) => {
    const date = `2026-09-${String(i + 1).padStart(2, '0')}`
    return day(date, draft({ work_date: date, hours: i === 13 ? '9.50' : '4.00' }))
  })

  it('正對照：依日期排時，那一天確實在第二頁', () => {
    const page1 = paginate(fourteen, {
      sort: (r) => sortWorklogRows(r, { key: 'date', direction: 'asc' }),
      page: 1,
      pageSize: 10,
    })
    expect(page1.items.map((r) => r.work_date)).not.toContain('2026-09-14')
  })

  it('🔴 依時數大→小排，第二頁的那一天出現在第一頁第一列', () => {
    const page1 = paginate(fourteen, {
      sort: (r) => sortWorklogRows(r, { key: 'hours', direction: 'desc' }),
      page: 1,
      pageSize: 10,
    })
    expect(page1.items[0].work_date).toBe('2026-09-14')
    expect(page1.total).toBe(14)
  })

  it('沒有草稿的天不論升降都排最後（不當成 0）', () => {
    const rows = [day('2026-09-01'), day('2026-09-02', draft({ hours: '2.00' })), day('2026-09-03', draft({ hours: '6.00' }))]
    expect(sortWorklogRows(rows, { key: 'hours', direction: 'asc' }).map((r) => r.work_date)).toEqual([
      '2026-09-02',
      '2026-09-03',
      '2026-09-01',
    ])
    expect(sortWorklogRows(rows, { key: 'hours', direction: 'desc' }).map((r) => r.work_date)).toEqual([
      '2026-09-03',
      '2026-09-02',
      '2026-09-01',
    ])
  })

  it('不改動傳入的陣列（它通常是 store 的 state）', () => {
    const rows = [day('2026-09-02'), day('2026-09-01')]
    sortWorklogRows(rows, { key: 'date', direction: 'asc' })
    expect(rows[0].work_date).toBe('2026-09-02')
  })
})

describe('比例條', () => {
  it('分母是表上最多的那天，不是 8 小時', () => {
    const rows = [day('a', draft({ hours: '5.00' })), day('b', draft({ hours: '10.00' })), day('c')]
    expect(maxDraftHours(rows)).toBe(10)
    expect(hoursRatio(5, 10)).toBe(0.5)
  })

  it('沒有數字、或整張表都是 0 → 0（不除以零）', () => {
    expect(hoursRatio(null, 8)).toBe(0)
    expect(hoursRatio(3, 0)).toBe(0)
    expect(maxDraftHours([day('a'), day('b', draft({ hours: 'abc' }))])).toBe(0)
  })
})

describe('🔴 專案分配（ADR-0008：只顯示 Viewer 已選定的）', () => {
  /** 產生當下的分群快照，裡面確實有系統建議的候選專案。 */
  const suggestedMeta = {
    clusters: [
      {
        key: 'c1',
        candidates: [{ issue_key: 'SUG-1', project_key: 'SUGGESTED-PRJ', title: '系統建議的專案' }],
      },
    ],
  }

  it('正對照前提：測試資料裡真的帶著建議專案', () => {
    const d = draft({ generation_meta: suggestedMeta })
    expect(JSON.stringify(d)).toContain('SUGGESTED-PRJ')
  })

  it('全部未選 → 「歸屬尚未選擇（N 段）」，不出現建議專案', () => {
    const label = assignmentLabel(assignmentOf(day('2026-09-07', draft({ generation_meta: suggestedMeta }))))
    expect(label).toBe(`${UNASSIGNED_LABEL}（3 段）`)
    expect(label).not.toContain('SUGGESTED-PRJ')
    expect(label).not.toContain('系統建議')
  })

  it('部分已選／全部已選只講段數', () => {
    expect(assignmentLabel(assignmentOf(day('d', draft({ unassigned_count: 1 }))))).toBe(
      `已選 2 段・${UNASSIGNED_LABEL} 1 段`,
    )
    expect(assignmentLabel(assignmentOf(day('d', draft({ unassigned_count: 0 }))))).toBe('已選 3 段')
  })

  it('沒有草稿、或 0 段 → —', () => {
    expect(assignmentLabel(assignmentOf(day('d')))).toBe('—')
    expect(assignmentLabel(assignmentOf(day('d', draft({ entry_count: 0, unassigned_count: 0 }))))).toBe('—')
  })

  it('認不出來的計數不夾成合理值（未選多於總數、負數、小數）', () => {
    expect(assignmentOf(day('d', draft({ entry_count: 2, unassigned_count: 3 }))).kind).toBe('invalid')
    expect(assignmentOf(day('d', draft({ unassigned_count: -1 }))).kind).toBe('invalid')
    expect(assignmentOf(day('d', draft({ entry_count: 2.5 }))).kind).toBe('invalid')
  })
})
