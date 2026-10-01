import { describe, expect, it } from 'vitest'
import {
  DEFAULT_LIST_SORT,
  mentionTruncation,
  parseListSort,
  sortMentions,
  sortSpaces,
  type SpaceSortOptions,
} from '@/lib/listSort'
import type { Mention, Space } from '@/lib/types'

function space(
  id: string,
  name: string,
  lastActiveTime: string | null,
  extra: Partial<Space> = {},
): Space {
  return { id, displayName: name, type: 'SPACE', lastActiveTime, ...extra }
}

const ids = (list: { id: string | number }[]) => list.map((x) => x.id)

const opts = (sort: SpaceSortOptions['sort'], pinnedFirst = false): SpaceSortOptions => ({
  sort,
  pinnedFirst,
})

/**
 * 重現 2026-09-30 Viewer 截圖的形狀：釘選的三個比較舊，未釘選那個 5 分鐘前才動過。
 * 後端給的順序刻意打亂，證明結果不依賴上游的順序。
 */
const SCREENSHOT = [
  space('dm', '私訊', '2026-09-30T10:00:00Z', { type: 'DIRECT_MESSAGE', pinned: true }),
  space('grp-old', '群組舊', '2026-09-29T14:00:00Z', { pinned: true }),
  space('grp-new', '群組新', '2026-09-30T11:55:00Z'),
  space('multi', '多人', '2026-09-30T11:00:00Z', { type: 'GROUP_CHAT', pinned: true }),
]

describe('sortSpaces', () => {
  it('預設（最後活動新→舊、不置頂）：5 分鐘前的排第一，不被釘選插隊', () => {
    expect(ids(sortSpaces(SCREENSHOT, DEFAULT_LIST_SORT.space))).toEqual([
      'grp-new',
      'multi',
      'dm',
      'grp-old',
    ])
  })

  it('預設值本身：新→舊、釘選置頂關閉', () => {
    expect(DEFAULT_LIST_SORT.space).toEqual({ sort: 'activity-desc', pinnedFirst: false })
  })

  it('最後活動（舊→新）', () => {
    expect(ids(sortSpaces(SCREENSHOT, opts('activity-asc')))).toEqual([
      'grp-old',
      'dm',
      'multi',
      'grp-new',
    ])
  })

  it('名稱：依中文排序規則，數字依數值比（第 2 在第 10 前）', () => {
    const items = [
      space('c', '第10組', null),
      space('a', '第2組', '2026-09-01T00:00:00Z'),
      space('b', 'alpha', '2026-09-02T00:00:00Z'),
      space('d', 'Beta', '2026-09-03T00:00:00Z'),
    ]
    const result = ids(sortSpaces(items, opts('name')))
    expect(result.indexOf('a')).toBeLessThan(result.indexOf('c'))
    // 大小寫不影響：alpha 在 Beta 前
    expect(result.indexOf('b')).toBeLessThan(result.indexOf('d'))
  })

  it('類型：群組 → 私訊 → 多人對話 → 其他，組內新→舊', () => {
    const items = [
      space('x', '怪', '2026-09-30T12:00:00Z', { type: 'SOMETHING_NEW' }),
      space('m', '多人', '2026-09-30T11:00:00Z', { type: 'GROUP_CHAT' }),
      space('d', '私訊', '2026-09-30T10:00:00Z', { type: 'DIRECT_MESSAGE' }),
      space('g1', '群組舊', '2026-09-01T00:00:00Z'),
      space('g2', '群組新', '2026-09-30T09:00:00Z'),
    ]
    expect(ids(sortSpaces(items, opts('type')))).toEqual(['g2', 'g1', 'd', 'm', 'x'])
  })

  it('名稱降冪：與升冪恰好相反（同名時依 id 決勝，所以整份可逆）', () => {
    const items = [
      space('c', '第10組', null),
      space('a', '第2組', '2026-09-01T00:00:00Z'),
      space('b', 'alpha', '2026-09-02T00:00:00Z'),
      space('d', 'Beta', '2026-09-03T00:00:00Z'),
      space('e', 'alpha', '2026-09-04T00:00:00Z'),
    ]
    const asc = ids(sortSpaces(items, opts('name')))
    expect(ids(sortSpaces(items, opts('name-desc')))).toEqual([...asc].reverse())
  })

  it('類型降冪：組的順序反過來，組內仍是新→舊（不是整份反轉）', () => {
    const items = [
      space('x', '怪', '2026-09-30T12:00:00Z', { type: 'SOMETHING_NEW' }),
      space('m', '多人', '2026-09-30T11:00:00Z', { type: 'GROUP_CHAT' }),
      space('d', '私訊', '2026-09-30T10:00:00Z', { type: 'DIRECT_MESSAGE' }),
      space('g1', '群組舊', '2026-09-01T00:00:00Z'),
      space('g2', '群組新', '2026-09-30T09:00:00Z'),
    ]
    // 整份反轉會得到 g1 在 g2 前；正確的是組內仍 g2（新）在前
    expect(ids(sortSpaces(items, opts('type-desc')))).toEqual(['x', 'm', 'd', 'g2', 'g1'])
  })

  it('釘選置頂開啟：釘選的在前，兩組內仍套用所選排序', () => {
    expect(ids(sortSpaces(SCREENSHOT, opts('activity-desc', true)))).toEqual([
      'multi',
      'dm',
      'grp-old',
      'grp-new',
    ])
    expect(ids(sortSpaces(SCREENSHOT, opts('activity-asc', true)))).toEqual([
      'grp-old',
      'dm',
      'multi',
      'grp-new',
    ])
  })

  it('沒有最後活動時間的一律排最後（新→舊與舊→新都是），彼此依名稱排', () => {
    const items = [
      space('n2', '乙', null),
      space('t1', 'T1', '2026-09-01T00:00:00Z'),
      space('bad', '甲', 'not-a-date'), // 解析不了也算沒有時間
      space('t2', 'T2', '2026-09-02T00:00:00Z'),
    ]
    const desc = ids(sortSpaces(items, opts('activity-desc')))
    const asc = ids(sortSpaces(items, opts('activity-asc')))
    expect(desc.slice(0, 2)).toEqual(['t2', 't1'])
    expect(asc.slice(0, 2)).toEqual(['t1', 't2'])
    // 尾端兩個的相對順序與方向無關（依名稱），證明它們是「排最後」而不是「當成最舊」
    expect(desc.slice(2)).toEqual(asc.slice(2))
    expect(new Set(desc.slice(2))).toEqual(new Set(['n2', 'bad']))
  })

  it('釘選置頂時，沒有時間的釘選 Space 排在釘選組的最後', () => {
    const items = [
      space('p-null', '釘無', null, { pinned: true }),
      space('p-t', '釘有', '2026-09-01T00:00:00Z', { pinned: true }),
      space('u', '未釘', '2026-09-30T00:00:00Z'),
    ]
    expect(ids(sortSpaces(items, opts('activity-desc', true)))).toEqual(['p-t', 'p-null', 'u'])
  })

  it('不改動傳入的陣列、不丟任何一筆', () => {
    const copy = [...SCREENSHOT]
    const result = sortSpaces(SCREENSHOT, opts('activity-desc', true))
    expect(SCREENSHOT).toEqual(copy)
    expect(result).toHaveLength(SCREENSHOT.length)
  })

  it('認不得的排序鍵退回預設（新→舊），不是原樣回傳', () => {
    const result = sortSpaces(SCREENSHOT, { sort: 'bogus' as never, pinnedFirst: false })
    expect(ids(result)[0]).toBe('grp-new')
  })
})

function mention(id: number, time: string, extra: Partial<Mention> = {}): Mention {
  return {
    id,
    space_id: 'spaces/x',
    space_name: 'X',
    message_name: `m${id}`,
    thread_name: null,
    sender_display: '某人',
    create_time: time,
    state: 'pending',
    resolved_at: null,
    ...extra,
  }
}

describe('sortMentions', () => {
  const items = [
    mention(1, '2026-09-28T00:00:00Z', { space_name: 'B 專案', sender_display: '王' }),
    mention(2, '2026-09-30T00:00:00Z', { space_name: 'A 專案', sender_display: '李' }),
    mention(3, '2026-09-29T00:00:00Z', { space_name: 'A 專案', sender_display: '王' }),
  ]

  it('時間（新→舊）', () => {
    expect(ids(sortMentions(items, 'time-desc'))).toEqual([2, 3, 1])
  })

  it('時間（舊→新）', () => {
    expect(ids(sortMentions(items, 'time-asc'))).toEqual([1, 3, 2])
  })

  it('依 Space：同 Space 聚在一起，組內新→舊', () => {
    expect(ids(sortMentions(items, 'space'))).toEqual([2, 3, 1])
  })

  it('依發言者：同一人聚在一起，組內新→舊', () => {
    const result = ids(sortMentions(items, 'sender'))
    // 王的兩則相鄰且新的在前；李自成一組
    const wang = result.filter((id) => id !== 2)
    expect(wang).toEqual([3, 1])
    expect(Math.abs(result.indexOf(3) - result.indexOf(1))).toBe(1)
  })

  it('同一時間維持傳入的順序（兩個方向都是），不另立平手規則', () => {
    const same = [mention(9, '2026-09-30T00:00:00Z'), mention(5, '2026-09-30T00:00:00Z')]
    expect(ids(sortMentions(same, 'time-desc'))).toEqual([9, 5])
    expect(ids(sortMentions(same, 'time-asc'))).toEqual([9, 5])
  })

  it('時間解析不了的排最後（兩個方向都是）', () => {
    const withBad = [...items, mention(4, '')]
    expect(ids(sortMentions(withBad, 'time-desc')).at(-1)).toBe(4)
    expect(ids(sortMentions(withBad, 'time-asc')).at(-1)).toBe(4)
  })

  it('不改動傳入的陣列', () => {
    const copy = [...items]
    sortMentions(items, 'time-asc')
    expect(items).toEqual(copy)
  })
})

describe('parseListSort', () => {
  it('沒存過（null）→ 預設值', () => {
    expect(parseListSort(null)).toEqual(DEFAULT_LIST_SORT)
  })

  it('不是 JSON → 預設值，不丟例外', () => {
    expect(parseListSort('{oops')).toEqual(DEFAULT_LIST_SORT)
  })

  it('合法內容原樣還原', () => {
    const saved = { space: { sort: 'name', pinnedFirst: true }, mention: 'sender' }
    expect(parseListSort(JSON.stringify(saved))).toEqual(saved)
  })

  it('向下相容：S5 之前存的四個排序鍵都原樣讀得回來', () => {
    for (const sort of ['activity-desc', 'activity-asc', 'name', 'type']) {
      const saved = { space: { sort, pinnedFirst: false }, mention: 'time-desc' }
      expect(parseListSort(JSON.stringify(saved)).space.sort).toBe(sort)
    }
  })

  it('新增的降冪鍵存進去讀得回來', () => {
    for (const sort of ['name-desc', 'type-desc']) {
      const saved = { space: { sort, pinnedFirst: true }, mention: 'space' }
      expect(parseListSort(JSON.stringify(saved))).toEqual(saved)
    }
  })

  it('認不得的方向寫法（例如 name-asc）照舊退回預設排序', () => {
    const saved = { space: { sort: 'name-asc', pinnedFirst: true }, mention: 'sender' }
    expect(parseListSort(JSON.stringify(saved))).toEqual({
      space: { sort: 'activity-desc', pinnedFirst: true },
      mention: 'sender',
    })
  })

  it('一欄壞掉只退那一欄，其他欄保留', () => {
    const saved = { space: { sort: 'renamed-key', pinnedFirst: true }, mention: 'time-asc' }
    expect(parseListSort(JSON.stringify(saved))).toEqual({
      space: { sort: 'activity-desc', pinnedFirst: true },
      mention: 'time-asc',
    })
  })

  it('pinnedFirst 不是布林（例如字串 "true"）→ 退回預設的 false', () => {
    const saved = { space: { sort: 'name', pinnedFirst: 'true' }, mention: 'nope' }
    expect(parseListSort(JSON.stringify(saved))).toEqual({
      space: { sort: 'name', pinnedFirst: false },
      mention: 'time-desc',
    })
  })
})

describe('mentionTruncation', () => {
  it('總數大於已載入 → 回報兩個數字', () => {
    expect(mentionTruncation({ pending: 30, resolved: 320 }, 200)).toEqual({
      loaded: 200,
      total: 350,
    })
  })

  it('（正對照）總數等於已載入 → 沒有截斷', () => {
    expect(mentionTruncation({ pending: 0, resolved: 89 }, 89)).toBeNull()
  })

  it('已載入比總數多（例如剛就地補進一則）→ 不算截斷', () => {
    expect(mentionTruncation({ pending: 1, resolved: 0 }, 2)).toBeNull()
  })
})
