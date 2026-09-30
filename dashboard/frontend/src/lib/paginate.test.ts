import { describe, expect, it } from 'vitest'
import {
  ariaSortOf,
  clampPage,
  DEFAULT_PAGE_SIZE,
  pageCountOf,
  pageTokens,
  paginate,
  parsePageSize,
  toggleSort,
} from '@/lib/paginate'
import { sortSpaces } from '@/lib/listSort'
import type { Space } from '@/lib/types'

interface Row {
  id: number
  name: string
  /** 越大越新 */
  at: number
  kind: 'a' | 'b'
}

/**
 * 30 列，照 id 順序餵進來，時間是 id 的反向——也就是「傳入順序」恰好是舊→新
 * 的反面。第 26～30 列在傳入順序下只會出現在第二頁（每頁 25）。
 */
function rows(n = 30): Row[] {
  return Array.from({ length: n }, (_, i) => ({
    id: i + 1,
    name: `第 ${i + 1} 列`,
    at: 1000 - i,
    kind: 'a' as const,
  }))
}

const newestFirst = (xs: readonly Row[]) => [...xs].sort((a, b) => b.at - a.at)
const oldestFirst = (xs: readonly Row[]) => [...xs].sort((a, b) => a.at - b.at)

describe('🔴 排序與篩選作用在全部資料上（第二頁資料當正對照）', () => {
  // 這一組就是規格 §4.2 要的守門。實作若退化成「先切頁、再排序／篩選」，
  // 第二頁那幾列永遠擠不進第一頁，下面每一條都會紅。
  // 2026-10-01 實測：把 paginate 改成先 slice 再 sort／filter，這組 4 條全紅；改回後全綠。

  it('排序：只在第二頁的最舊那一列，換成舊→新後出現在第一頁第一列', () => {
    const data = rows()
    // 前提自我檢查：不排序時 id=30 確實不在第一頁
    const unsorted = paginate(data, { page: 1, pageSize: 25 })
    expect(unsorted.items.map((r) => r.id)).not.toContain(30)

    const page1 = paginate(data, { sort: oldestFirst, page: 1, pageSize: 25 })
    expect(page1.items[0].id).toBe(30)
    expect(page1.items.map((r) => r.id)).toEqual(
      Array.from({ length: 25 }, (_, i) => 30 - i),
    )
  })

  it('篩選：符合條件的只有第二頁的列 → 篩選後它們出現在第一頁，總數算得到', () => {
    const data = rows().map((r) => (r.id >= 27 ? { ...r, kind: 'b' as const } : r))
    const page1 = paginate(data, { filter: (r) => r.kind === 'b', page: 1, pageSize: 25 })
    expect(page1.items.map((r) => r.id)).toEqual([27, 28, 29, 30])
    expect(page1.total).toBe(4)
    expect(page1.pageCount).toBe(1)
  })

  it('篩選＋排序一起：先篩全部、再排全部、最後切頁', () => {
    // 60 列裡偶數 id 是 b（30 列），舊→新排；第一頁的第一列必須是全部 b 裡最舊的 id=60，
    // 它在傳入順序裡是最後一列（第三頁）
    const data = rows(60).map((r) => ({ ...r, kind: r.id % 2 === 0 ? ('b' as const) : ('a' as const) }))
    const result = paginate(data, {
      filter: (r) => r.kind === 'b',
      sort: oldestFirst,
      page: 1,
      pageSize: 25,
    })
    expect(result.total).toBe(30)
    expect(result.pageCount).toBe(2)
    expect(result.items[0].id).toBe(60)
    const page2 = paginate(data, { filter: (r) => r.kind === 'b', sort: oldestFirst, page: 2, pageSize: 25 })
    expect(page2.items.map((r) => r.id)).toEqual([10, 8, 6, 4, 2])
    expect(page2.offset).toBe(25)
  })

  it('接得上 lib/listSort 的 sortSpaces：第二頁那個最新的 Space 排到第一頁第一列', () => {
    const spaces: Space[] = Array.from({ length: 30 }, (_, i) => ({
      id: `spaces/${i + 1}`,
      displayName: `Space ${i + 1}`,
      type: 'SPACE',
      // 第 30 個最新；其餘依 id 由新到舊。傳入順序下它在第二頁
      lastActiveTime: new Date(Date.UTC(2026, 8, 1, 0, i === 29 ? 59 : 30 - i)).toISOString(),
      pinned: false,
    })) as unknown as Space[]
    const result = paginate(spaces, {
      sort: (xs) => sortSpaces(xs, { sort: 'activity-desc', pinnedFirst: false }),
      page: 1,
      pageSize: 25,
    })
    expect(result.items[0].id).toBe('spaces/30')
  })
})

describe('paginate 的其他行為', () => {
  it('total 是篩選後的總數，不是當頁筆數', () => {
    const result = paginate(rows(60), { page: 1, pageSize: 25 })
    expect(result.items).toHaveLength(25)
    expect(result.total).toBe(60)
    expect(result.pageCount).toBe(3)
  })

  it('最後一頁不滿也照給', () => {
    const result = paginate(rows(60), { sort: newestFirst, page: 3, pageSize: 25 })
    expect(result.items.map((r) => r.id)).toEqual([51, 52, 53, 54, 55, 56, 57, 58, 59, 60])
  })

  it('頁碼超出新的總頁數 → 夾回最後一頁，而不是回空陣列', () => {
    const result = paginate(rows(30), { page: 4, pageSize: 10 })
    expect(result.page).toBe(3)
    expect(result.items.map((r) => r.id)).toEqual([21, 22, 23, 24, 25, 26, 27, 28, 29, 30])
  })

  it('篩選到 0 筆 → 第 1 頁、共 1 頁、空清單', () => {
    const result = paginate(rows(), { filter: () => false, page: 3, pageSize: 25 })
    expect(result).toEqual({ items: [], page: 1, pageCount: 1, total: 0, offset: 0 })
  })

  it('不改動傳入的陣列', () => {
    const data = rows()
    const snapshot = data.map((r) => r.id)
    paginate(data, { sort: oldestFirst, filter: (r) => r.id > 3, page: 1, pageSize: 10 })
    expect(data.map((r) => r.id)).toEqual(snapshot)
  })

  it('pageSize 不合法（0、負數）→ 退回預設，不會算出 Infinity 頁', () => {
    const result = paginate(rows(60), { page: 1, pageSize: 0 })
    expect(result.items).toHaveLength(DEFAULT_PAGE_SIZE)
    expect(result.pageCount).toBe(3)
  })
})

describe('頁碼計算', () => {
  it('pageCountOf：0 筆也是 1 頁；整除與不整除', () => {
    expect(pageCountOf(0, 25)).toBe(1)
    expect(pageCountOf(25, 25)).toBe(1)
    expect(pageCountOf(26, 25)).toBe(2)
  })

  it('clampPage：夾進 1..pageCount，NaN 與小數也收掉', () => {
    expect(clampPage(0, 3)).toBe(1)
    expect(clampPage(9, 3)).toBe(3)
    expect(clampPage(Number.NaN, 3)).toBe(1)
    expect(clampPage(2.7, 3)).toBe(2)
  })

  it('pageTokens：頭尾與目前頁前後各一，其餘折成省略號；只差一頁直接補', () => {
    expect(pageTokens(1, 1)).toEqual([1])
    expect(pageTokens(1, 3)).toEqual([1, 2, 3])
    expect(pageTokens(5, 10)).toEqual([1, 'gap', 4, 5, 6, 'gap', 10])
    expect(pageTokens(3, 10)).toEqual([1, 2, 3, 4, 'gap', 10])
    expect(pageTokens(10, 10)).toEqual([1, 'gap', 9, 10])
  })
})

describe('parsePageSize', () => {
  it('只接受 10／25／50（數字或字串）', () => {
    expect(parsePageSize(10)).toBe(10)
    expect(parsePageSize('50')).toBe(50)
    expect(parsePageSize(20)).toBeNull()
    expect(parsePageSize('abc')).toBeNull()
  })

  it('⚠️ null 與空字串不會變成 0（Base UI Select 清除選擇時給 null）', () => {
    expect(parsePageSize(null)).toBeNull()
    expect(parsePageSize('')).toBeNull()
    expect(parsePageSize(undefined)).toBeNull()
  })
})

describe('欄位排序狀態', () => {
  it('點新欄用起始方向；點同一欄反轉；沒有「取消排序」', () => {
    const s1 = toggleSort(null, 'name')
    expect(s1).toEqual({ key: 'name', direction: 'asc' })
    const s2 = toggleSort(s1, 'name')
    expect(s2).toEqual({ key: 'name', direction: 'desc' })
    expect(toggleSort(s2, 'name')).toEqual({ key: 'name', direction: 'asc' })
    expect(toggleSort(s2, 'time', 'desc')).toEqual({ key: 'time', direction: 'desc' })
  })

  it('aria-sort 只出現在正在排的那一欄', () => {
    const s = { key: 'name', direction: 'desc' } as const
    expect(ariaSortOf(s, 'name')).toBe('descending')
    expect(ariaSortOf(s, 'time')).toBeUndefined()
    expect(ariaSortOf(null, 'name')).toBeUndefined()
  })
})
