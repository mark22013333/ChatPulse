import { describe, expect, it } from 'vitest'
import { filterSpaces, sortByPinned } from '@/store/spaces'
import type { Space } from '@/lib/types'

function space(id: string, name: string, pinned?: boolean): Space {
  return {
    id,
    displayName: name,
    type: 'SPACE',
    lastActiveTime: '2026-09-01T00:00:00Z',
    pinned,
  } as Space
}

describe('sortByPinned', () => {
  it('釘選的排到最前面', () => {
    const items = [space('a', 'A'), space('b', 'B', true), space('c', 'C')]
    expect(sortByPinned(items).map((s) => s.id)).toEqual(['b', 'a', 'c'])
  })

  it('釘選之間、未釘選之間都維持原順序（後端已依最後活動排好）', () => {
    const items = [
      space('a', 'A'),
      space('b', 'B', true),
      space('c', 'C'),
      space('d', 'D', true),
    ]
    expect(sortByPinned(items).map((s) => s.id)).toEqual(['b', 'd', 'a', 'c'])
  })

  it('沒有任何釘選時原樣回傳同一個陣列（省掉 436 筆的無謂複製）', () => {
    const items = [space('a', 'A'), space('b', 'B')]
    expect(sortByPinned(items)).toBe(items)
  })

  it('空陣列不會爆', () => {
    expect(sortByPinned([])).toEqual([])
  })

  it('不改動傳入的陣列', () => {
    const items = [space('a', 'A'), space('b', 'B', true)]
    const copy = [...items]
    sortByPinned(items)
    expect(items).toEqual(copy)
  })
})

describe('filterSpaces 與 sortByPinned 是兩件事', () => {
  it('filterSpaces 只過濾、不排序——排序責任在 sortByPinned', () => {
    const items = [space('a', 'Alpha'), space('b', 'Beta', true)]
    expect(filterSpaces(items, '').map((s) => s.id)).toEqual(['a', 'b'])
    expect(sortByPinned(filterSpaces(items, '')).map((s) => s.id)).toEqual(['b', 'a'])
  })

  it('組合起來：先過濾再把釘選排前面', () => {
    const items = [
      space('a', '後端維運'),
      space('b', '前端維運', true),
      space('c', '行銷'),
    ]
    expect(sortByPinned(filterSpaces(items, '維運')).map((s) => s.id)).toEqual(['b', 'a'])
  })
})
