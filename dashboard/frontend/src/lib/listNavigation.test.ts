import { describe, expect, it } from 'vitest'
import { LIST_PAGE_SIZE, nextListIndex } from './listNavigation'

const COUNT = 436 // 實際的 Space 數量

describe('nextListIndex', () => {
  it('↑↓ 走一列', () => {
    expect(nextListIndex('ArrowDown', 0, COUNT)).toBe(1)
    expect(nextListIndex('ArrowUp', 5, COUNT)).toBe(4)
  })

  it('Home／End 直達兩端', () => {
    expect(nextListIndex('Home', 200, COUNT)).toBe(0)
    expect(nextListIndex('End', 200, COUNT)).toBe(COUNT - 1)
  })

  it('PageUp／PageDown 跳一頁', () => {
    expect(nextListIndex('PageDown', 0, COUNT)).toBe(LIST_PAGE_SIZE)
    expect(nextListIndex('PageUp', 100, COUNT)).toBe(100 - LIST_PAGE_SIZE)
  })

  it('**邊界夾住，不繞回另一端**', () => {
    // 按住 ↓ 一路到底不該跳回第一列——436 筆的清單繞回去等於失去方位感
    expect(nextListIndex('ArrowDown', COUNT - 1, COUNT)).toBe(COUNT - 1)
    expect(nextListIndex('ArrowUp', 0, COUNT)).toBe(0)
    expect(nextListIndex('PageDown', COUNT - 3, COUNT)).toBe(COUNT - 1)
    expect(nextListIndex('PageUp', 3, COUNT)).toBe(0)
  })

  it('current 超出範圍時先夾回合法區間（搜尋過濾後會發生）', () => {
    // 原本停在第 300 列，搜尋只剩 5 筆
    expect(nextListIndex('ArrowDown', 300, 5)).toBe(4)
    expect(nextListIndex('ArrowUp', 300, 5)).toBe(3)
    expect(nextListIndex('ArrowUp', -7, 5)).toBe(0)
  })

  it('空清單一律回 null', () => {
    expect(nextListIndex('ArrowDown', 0, 0)).toBeNull()
    expect(nextListIndex('Home', 0, 0)).toBeNull()
  })

  it('**不是導航鍵就回 null**，呼叫端才知道不要 preventDefault', () => {
    // 這條顧的是打字：清單裡按 a／Tab／Escape 都不該被吃掉
    expect(nextListIndex('a', 0, COUNT)).toBeNull()
    expect(nextListIndex('Tab', 0, COUNT)).toBeNull()
    expect(nextListIndex('Escape', 0, COUNT)).toBeNull()
    expect(nextListIndex('Enter', 0, COUNT)).toBeNull()
  })

  it('單筆清單怎麼按都停在 0', () => {
    for (const key of ['ArrowDown', 'ArrowUp', 'Home', 'End', 'PageDown', 'PageUp']) {
      expect(nextListIndex(key, 0, 1)).toBe(0)
    }
  })
})
