import { describe, expect, it } from 'vitest'
import {
  DEFAULT_ROUTE,
  fromSpaceKey,
  hashForMentions,
  hashForSettings,
  hashForSummary,
  parseHash,
  sameRoute,
  toSpaceKey,
} from '@/lib/route'

describe('toSpaceKey / fromSpaceKey', () => {
  it('砍掉 spaces/ 前綴，讓網址短到可以貼給同事', () => {
    expect(toSpaceKey('spaces/AAAAxLxqJxY')).toBe('AAAAxLxqJxY')
  })

  it('來回轉換要回到原值', () => {
    const id = 'spaces/AAAAxLxqJxY'
    expect(fromSpaceKey(toSpaceKey(id))).toBe(id)
  })

  it('不合形狀的 id 用百分比編碼，不要讓路由壞掉', () => {
    const weird = 'dm/與/斜線'
    expect(fromSpaceKey(toSpaceKey(weird))).toBe('spaces/dm/與/斜線')
  })

  it('已經帶前綴的片段不會被疊第二層', () => {
    expect(fromSpaceKey('spaces/AAA')).toBe('spaces/AAA')
  })

  it('空字串進空字串出', () => {
    expect(toSpaceKey('')).toBe('')
    expect(fromSpaceKey('')).toBe('')
  })

  it('壞掉的百分比編碼不會丟例外', () => {
    expect(() => fromSpaceKey('%E0%A4%A')).not.toThrow()
  })
})

describe('parseHash', () => {
  it('空 hash 是摘要工作台', () => {
    expect(parseHash('')).toEqual(DEFAULT_ROUTE)
    expect(parseHash('#/')).toEqual(DEFAULT_ROUTE)
  })

  it('看不懂的路徑退回摘要工作台，而不是空白畫面', () => {
    expect(parseHash('#/whatever/nonsense').section).toBe('summary')
  })

  it('摘要帶 Space', () => {
    const r = parseHash('#/summary/AAAAxLxqJxY')
    expect(r.section).toBe('summary')
    expect(r.spaceId).toBe('spaces/AAAAxLxqJxY')
  })

  it('摘要的 query 只覆寫這一次，不進偏好', () => {
    const r = parseHash('#/summary/AAA?style=technical&limit=200')
    expect(r.query).toEqual({ style: 'technical', limit: '200' })
  })

  it('收件匣帶 mention id', () => {
    const r = parseHash('#/mentions/47')
    expect(r.section).toBe('mentions')
    expect(r.mentionId).toBe(47)
  })

  it('非數字的 mention id 當成沒選', () => {
    expect(parseHash('#/mentions/abc').mentionId).toBeNull()
    expect(parseHash('#/mentions/-3').mentionId).toBeNull()
    expect(parseHash('#/mentions/0').mentionId).toBeNull()
  })

  it('merge 保留順序（第一個是主要那則）並去重', () => {
    expect(parseHash('#/mentions/47?merge=47,48,48,49').mergeIds).toEqual([47, 48, 49])
  })

  it('merge 裡的垃圾值被濾掉', () => {
    expect(parseHash('#/mentions/47?merge=47,abc,,0,-1,48').mergeIds).toEqual([47, 48])
  })

  it('設定頁預設落在回覆設定', () => {
    expect(parseHash('#/settings').settingsTab).toBe('reply')
    expect(parseHash('#/settings/不存在的分頁').settingsTab).toBe('reply')
  })

  it('設定頁的每個分頁都認得', () => {
    expect(parseHash('#/settings/diagnostics').settingsTab).toBe('diagnostics')
    expect(parseHash('#/settings/code-projects').settingsTab).toBe('code-projects')
  })

  it('設定頁帶單筆 id', () => {
    const r = parseHash('#/settings/personas/3')
    expect(r.settingsTab).toBe('personas')
    expect(r.settingsId).toBe('3')
  })
})

describe('hashFor*', () => {
  it('摘要', () => {
    expect(hashForSummary()).toBe('#/summary')
    expect(hashForSummary('spaces/AAA')).toBe('#/summary/AAA')
    expect(hashForSummary('spaces/AAA', { style: 'technical' })).toBe(
      '#/summary/AAA?style=technical',
    )
    expect(hashForSummary('spaces/AAA', { style: undefined })).toBe('#/summary/AAA')
  })

  it('收件匣：只有真的合併（兩則以上）才帶 merge', () => {
    expect(hashForMentions()).toBe('#/mentions')
    expect(hashForMentions(47)).toBe('#/mentions/47')
    expect(hashForMentions(47, [47])).toBe('#/mentions/47')
    expect(hashForMentions(47, [47, 48])).toBe('#/mentions/47?merge=47%2C48')
  })

  it('設定', () => {
    expect(hashForSettings()).toBe('#/settings/reply')
    expect(hashForSettings('personas', 3)).toBe('#/settings/personas/3')
    expect(hashForSettings('personas', null)).toBe('#/settings/personas')
  })

  it('產生的 hash 解析回去要一致', () => {
    const h = hashForMentions(47, [47, 48])
    const r = parseHash(h)
    expect(r.mentionId).toBe(47)
    expect(r.mergeIds).toEqual([47, 48])
  })
})

describe('sameRoute', () => {
  it('同一個位置', () => {
    expect(sameRoute(parseHash('#/mentions/47'), parseHash('#/mentions/47'))).toBe(true)
  })

  it('merge 的順序不同就不是同一個位置（第一個是主要那則）', () => {
    expect(
      sameRoute(parseHash('#/mentions/47?merge=47,48'), parseHash('#/mentions/47?merge=48,47')),
    ).toBe(false)
  })

  it('不同 section', () => {
    expect(sameRoute(parseHash('#/summary'), parseHash('#/mentions'))).toBe(false)
  })
})
