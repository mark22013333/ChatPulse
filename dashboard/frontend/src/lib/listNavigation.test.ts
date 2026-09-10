import { describe, expect, it } from 'vitest'
import { LIST_PAGE_SIZE, nextListIndex, stepId } from './listNavigation'

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

/** `[`／`]` 的計算層（規格 §11.1：上一則／下一則 Mention）。 */
describe('stepId', () => {
  const LIST = [{ id: 47 }, { id: 48 }, { id: 65 }]

  it('往後與往前各走一格', () => {
    expect(stepId(LIST, 47, 1)).toBe(48)
    expect(stepId(LIST, 48, 1)).toBe(65)
    expect(stepId(LIST, 65, -1)).toBe(48)
  })

  it('**邊界夾住、不繞回去**', () => {
    // 繞回會讓人失去「我在哪」的感覺，而按 [ ] 時左欄不一定看得見，
    // 從最後一則跳回第一則會更莫名其妙
    expect(stepId(LIST, 65, 1)).toBeNull()
    expect(stepId(LIST, 47, -1)).toBeNull()
  })

  it('還沒選任何一則時，] 給第一則、[ 給最後一則', () => {
    expect(stepId(LIST, null, 1)).toBe(47)
    expect(stepId(LIST, null, -1)).toBe(65)
  })

  it('**目前這一則不在清單裡就回 null**', () => {
    // 真實情境：剛把它標成已處理，它從待處理分頁消失了；或者它是摘要
    // 工作台挑的 manual 而現在看的是已處理分頁。這時亂跳到第一則會讓
    // 使用者以為自己按錯鍵
    expect(stepId(LIST, 999, 1)).toBeNull()
    expect(stepId(LIST, 999, -1)).toBeNull()
  })

  it('空清單一律回 null', () => {
    expect(stepId([], 47, 1)).toBeNull()
    expect(stepId([], null, 1)).toBeNull()
  })

  it('單筆清單怎麼按都到不了別處', () => {
    expect(stepId([{ id: 47 }], 47, 1)).toBeNull()
    expect(stepId([{ id: 47 }], 47, -1)).toBeNull()
  })
})
