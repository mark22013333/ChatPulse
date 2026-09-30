import { describe, expect, it } from 'vitest'
import {
  DEFAULT_SPACE_TABLE_FILTERS,
  headerSortOf,
  lastActiveLabel,
  nextSpaceSort,
  nextSummarySort,
  outstandingBySpace,
  parseZonedTime,
  sortBySummaryStatus,
  summaryStatusOf,
  parseSpaceTab,
  parseSpaceTypeFilter,
  spaceTableFilter,
} from '@/lib/spaceTable'
import { isOutstanding } from '@/store/mentions'
import type { Mention, MentionStateValue, Space } from '@/lib/types'

function space(id: string, over: Partial<Space> = {}): Space {
  return { id, displayName: id, type: 'SPACE', lastActiveTime: null, ...over }
}

function mention(id: number, spaceId: string, state: MentionStateValue): Mention {
  return {
    id,
    space_id: spaceId,
    space_name: spaceId,
    message_name: `m${id}`,
    thread_name: null,
    sender_display: 'x',
    create_time: '2026-09-30T00:00:00Z',
    state,
    resolved_at: null,
  }
}

describe('outstandingBySpace', () => {
  it('只算待處理：pending 與 manual 都算、resolved 不算（與收件匣同一個判準）', () => {
    const counts = outstandingBySpace(
      [
        mention(1, 'spaces/a', 'pending'),
        mention(2, 'spaces/a', 'manual'),
        mention(3, 'spaces/a', 'resolved'),
        mention(4, 'spaces/b', 'resolved'),
      ],
      isOutstanding,
    )
    expect(counts.get('spaces/a')).toBe(2)
    // 只有已處理的 Space 不出現在表裡——「有新 Mention」分頁靠這件事判斷
    expect(counts.has('spaces/b')).toBe(false)
  })
})

describe('spaceTableFilter', () => {
  const items = [
    space('spaces/a', { displayName: '後端值班', pinned: true }),
    space('spaces/b', { displayName: '王小明', type: 'DIRECT_MESSAGE' }),
    space('spaces/c', { displayName: '舊式私訊', type: 'DM' }),
    space('spaces/d', { displayName: 'PM 對齊', type: 'GROUP_CHAT' }),
    space('spaces/e', { displayName: '奇怪的', type: 'SOMETHING_NEW' }),
  ]
  const none = new Map<string, number>()
  const ids = (filter: (s: Space) => boolean) => items.filter(filter).map((s) => s.id)

  it('預設條件全部留下——包括認不得的類型（丟掉就是靜默少給資料）', () => {
    expect(ids(spaceTableFilter(DEFAULT_SPACE_TABLE_FILTERS, none))).toHaveLength(items.length)
  })

  it('已釘選分頁只留 pinned', () => {
    expect(ids(spaceTableFilter({ ...DEFAULT_SPACE_TABLE_FILTERS, tab: 'pinned' }, none))).toEqual([
      'spaces/a',
    ])
  })

  it('有新 Mention 分頁只留待處理數 > 0 的', () => {
    const outstanding = new Map([
      ['spaces/b', 2],
      ['spaces/d', 0],
    ])
    expect(
      ids(spaceTableFilter({ ...DEFAULT_SPACE_TABLE_FILTERS, tab: 'mentions' }, outstanding)),
    ).toEqual(['spaces/b'])
  })

  it('類型「私訊」同時涵蓋 DIRECT_MESSAGE 與 DM 兩種寫法', () => {
    expect(ids(spaceTableFilter({ ...DEFAULT_SPACE_TABLE_FILTERS, type: '私訊' }, none))).toEqual([
      'spaces/b',
      'spaces/c',
    ])
  })

  it('搜尋不分大小寫、忽略前後空白', () => {
    expect(ids(spaceTableFilter({ ...DEFAULT_SPACE_TABLE_FILTERS, search: '  pm ' }, none))).toEqual([
      'spaces/d',
    ])
  })
})

describe('表頭排序與 listSort 排序鍵的對應', () => {
  it('headerSortOf 把四個排序鍵都翻得出來', () => {
    expect(headerSortOf('activity-desc')).toEqual({ key: 'activity', direction: 'desc' })
    expect(headerSortOf('activity-asc')).toEqual({ key: 'activity', direction: 'asc' })
    expect(headerSortOf('name')).toEqual({ key: 'name', direction: 'asc' })
    expect(headerSortOf('type')).toEqual({ key: 'type', direction: 'asc' })
  })

  it('最後活動：從別欄點過來是新→舊，再點一次反轉', () => {
    expect(nextSpaceSort('name', 'activity')).toBe('activity-desc')
    expect(nextSpaceSort('activity-desc', 'activity')).toBe('activity-asc')
    expect(nextSpaceSort('activity-asc', 'activity')).toBe('activity-desc')
  })

  it('名稱／類型只有一個方向，再點一次維持不變', () => {
    expect(nextSpaceSort('activity-desc', 'name')).toBe('name')
    expect(nextSpaceSort('name', 'name')).toBe('name')
    expect(nextSpaceSort('type', 'type')).toBe('type')
  })
})

describe('lastActiveLabel', () => {
  const now = new Date(2026, 9, 1, 15, 0) // 2026-10-01 15:00 本地時間

  it('今天顯示時間', () => {
    expect(lastActiveLabel(new Date(2026, 9, 1, 9, 5).toISOString(), now)).toBe('09:05')
  })

  it('更早顯示日期；同一年不帶年份', () => {
    expect(lastActiveLabel(new Date(2026, 8, 28, 23, 0).toISOString(), now)).toBe('9/28')
  })

  it('跨年才帶年份', () => {
    expect(lastActiveLabel(new Date(2025, 8, 28).toISOString(), now)).toBe('2025/9/28')
  })

  it('沒有值或解析不了一律「—」，不猜', () => {
    expect(lastActiveLabel(null, now)).toBe('—')
    expect(lastActiveLabel('not-a-date', now)).toBe('—')
  })
})

describe('parse 函式認不得就回 null', () => {
  it('tab 與類型', () => {
    expect(parseSpaceTab('pinned')).toBe('pinned')
    expect(parseSpaceTab(null)).toBeNull()
    expect(parseSpaceTypeFilter('群組')).toBe('群組')
    expect(parseSpaceTypeFilter('SPACE')).toBeNull()
  })
})

describe('summaryStatusOf：三種狀態', () => {
  const at = '2026-09-02T00:00:00+00:00'

  it('lastSummaryAt 為 null → 從未產生（不論有沒有活動時間）', () => {
    expect(summaryStatusOf({ lastActiveTime: '2026-09-03T00:00:00Z', lastSummaryAt: null })).toBe('never')
    expect(summaryStatusOf({ lastActiveTime: null, lastSummaryAt: null })).toBe('never')
  })

  it('活動晚於 Summary → 有新活動；早於或同時 → 已是最新', () => {
    expect(summaryStatusOf({ lastActiveTime: '2026-09-02T00:00:01Z', lastSummaryAt: at })).toBe('stale')
    expect(summaryStatusOf({ lastActiveTime: '2026-09-01T23:59:59Z', lastSummaryAt: at })).toBe('fresh')
    expect(summaryStatusOf({ lastActiveTime: '2026-09-02T00:00:00Z', lastSummaryAt: at })).toBe('fresh')
  })

  it('🔴 lastActiveTime 缺少或解析不了 → unknown，不准猜成已是最新', () => {
    expect(summaryStatusOf({ lastActiveTime: null, lastSummaryAt: at })).toBe('unknown')
    expect(summaryStatusOf({ lastActiveTime: '', lastSummaryAt: at })).toBe('unknown')
    expect(summaryStatusOf({ lastActiveTime: '昨天', lastSummaryAt: at })).toBe('unknown')
  })

  it('lastSummaryAt 欄位缺少（舊後端）或解析不了 → unknown，不當成從未產生', () => {
    expect(summaryStatusOf({ lastActiveTime: '2026-09-03T00:00:00Z' })).toBe('unknown')
    expect(summaryStatusOf({ lastActiveTime: '2026-09-03T00:00:00Z', lastSummaryAt: 'garbage' })).toBe('unknown')
  })
})

describe('summaryStatusOf：時區與表示法', () => {
  it('同一瞬間、表示法不同（Z＋微秒 vs +00:00）→ 不算有新活動', () => {
    expect(
      summaryStatusOf({ lastActiveTime: '2026-09-02T00:00:00.000000Z', lastSummaryAt: '2026-09-02T00:00:00+00:00' }),
    ).toBe('fresh')
  })

  it('同一瞬間、時區不同（UTC vs +08:00）→ 不算有新活動', () => {
    expect(
      summaryStatusOf({ lastActiveTime: '2026-09-02T00:00:00Z', lastSummaryAt: '2026-09-02T08:00:00+08:00' }),
    ).toBe('fresh')
  })

  it('正對照：字串看起來較早、實際較晚（+08:00 的 07:00 ＝ UTC 前一天 23:00）時，以實際時間點判斷', () => {
    // 字串比較會說 "2026-09-01T23:30:00Z" < "2026-09-02T07:00:00+08:00"（已是最新），
    // 實際上活動在 23:30Z、Summary 在 23:00Z，活動比較晚
    expect(
      summaryStatusOf({ lastActiveTime: '2026-09-01T23:30:00Z', lastSummaryAt: '2026-09-02T07:00:00+08:00' }),
    ).toBe('stale')
  })

  it('Google 的微秒精度：同一秒內晚 0.77 秒的活動仍算有新活動（Summary 只存到秒，誤差方向保守）', () => {
    expect(
      summaryStatusOf({ lastActiveTime: '2026-09-02T00:00:00.773695Z', lastSummaryAt: '2026-09-02T00:00:00+00:00' }),
    ).toBe('stale')
  })
})

describe('parseZonedTime', () => {
  it('接受 Z 與 ±hh:mm，含小數秒', () => {
    expect(parseZonedTime('2026-09-02T00:00:00Z')).toBe(Date.UTC(2026, 8, 2))
    expect(parseZonedTime('2026-09-02T00:00:00+00:00')).toBe(Date.UTC(2026, 8, 2))
    expect(parseZonedTime('2026-09-02T08:00:00.5+08:00')).toBe(Date.UTC(2026, 8, 2) + 500)
  })

  it('🔴 沒有時區標記一律拒絕（Date.parse 會當成本地時間，靜默差 8 小時）', () => {
    expect(parseZonedTime('2026-09-02T00:00:00')).toBeNull()
    expect(parseZonedTime('2026-09-02')).toBeNull()
    expect(parseZonedTime('2026-09-02 00:00:00+00:00')).toBeNull()
    expect(parseZonedTime(null)).toBeNull()
    expect(parseZonedTime(undefined)).toBeNull()
  })
})

describe('sortBySummaryStatus', () => {
  const at = '2026-09-02T00:00:00+00:00'
  const list = [
    space('fresh', { lastActiveTime: '2026-09-01T00:00:00Z', lastSummaryAt: at }),
    space('unknown', { lastActiveTime: null, lastSummaryAt: at }),
    space('never-old', { lastActiveTime: '2026-08-01T00:00:00Z', lastSummaryAt: null }),
    space('stale', { lastActiveTime: '2026-09-03T00:00:00Z', lastSummaryAt: at, pinned: true }),
    space('never-new', { lastActiveTime: '2026-09-05T00:00:00Z', lastSummaryAt: null }),
  ]
  const ids = (xs: Space[]) => xs.map((s) => s.id)

  it('升冪：有新活動 → 從未產生 → 已是最新；同狀態依最後活動新→舊；unknown 最後', () => {
    expect(ids(sortBySummaryStatus(list, 'asc', false))).toEqual([
      'stale',
      'never-new',
      'never-old',
      'fresh',
      'unknown',
    ])
  })

  it('降冪反轉狀態順序，但 unknown 仍然排最後', () => {
    expect(ids(sortBySummaryStatus(list, 'desc', false))).toEqual([
      'fresh',
      'never-new',
      'never-old',
      'stale',
      'unknown',
    ])
  })

  it('釘選置頂照舊生效；不改動傳入的陣列', () => {
    const before = ids(list)
    expect(ids(sortBySummaryStatus(list, 'desc', true))[0]).toBe('stale')
    expect(ids(list)).toEqual(before)
  })
})

describe('Summary 欄的表頭排序', () => {
  it('summarySort 有值時表頭標在 Summary 欄；沒有時照 listSort 的鍵', () => {
    expect(headerSortOf('name', 'asc')).toEqual({ key: 'summary', direction: 'asc' })
    expect(headerSortOf('name', null)).toEqual({ key: 'name', direction: 'asc' })
  })

  it('第一次點＝升冪，再點一次反轉', () => {
    expect(nextSummarySort(null)).toBe('asc')
    expect(nextSummarySort('asc')).toBe('desc')
    expect(nextSummarySort('desc')).toBe('asc')
  })
})
