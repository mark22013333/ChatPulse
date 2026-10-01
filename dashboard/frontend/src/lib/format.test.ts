import { describe, expect, it } from 'vitest'
import { formatMonthDay, weekdayLabel } from './format'

/**
 * 工時表格用的兩個日期格式化函式。
 *
 * 這兩條原本只在舊週欄（`WorklogWeekRail`）的元件測試裡被間接涵蓋：
 * 「七天全部渲染」看得到週一～週日與 09/07、09/13，「weekday 走對照表」
 * 看得到 7 對到週日。週欄移除後改成直接測函式本身。
 */

describe('weekdayLabel：ISO 星期走對照表', () => {
  it('1～7 依序對到週一～週日', () => {
    expect([1, 2, 3, 4, 5, 6, 7].map(weekdayLabel)).toEqual([
      '週一',
      '週二',
      '週三',
      '週四',
      '週五',
      '週六',
      '週日',
    ])
  })

  it('**7 是週日，不是陣列索引錯位後的別的星期**', () => {
    expect(weekdayLabel(7)).toBe('週日')
  })

  it('查不到就回空字串，不編一個星期出來（0 與 8 都不是 ISO 星期）', () => {
    expect(weekdayLabel(0)).toBe('')
    expect(weekdayLabel(8)).toBe('')
  })
})

describe('formatMonthDay：純字串切割', () => {
  it('2026-09-07 → 09/07、2026-09-13 → 09/13', () => {
    expect(formatMonthDay('2026-09-07')).toBe('09/07')
    expect(formatMonthDay('2026-09-13')).toBe('09/13')
  })

  it('月初不會因時區被推回前一天（不經過 Date）', () => {
    expect(formatMonthDay('2026-10-01')).toBe('10/01')
  })

  it('不是 YYYY-MM-DD 就原樣回傳', () => {
    expect(formatMonthDay('2026/09/07')).toBe('2026/09/07')
    expect(formatMonthDay('')).toBe('')
  })
})
