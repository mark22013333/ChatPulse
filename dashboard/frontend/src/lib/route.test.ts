import { describe, expect, it } from 'vitest'
import {
  DEFAULT_ROUTE,
  fromSpaceKey,
  hasDetail,
  hashForMentions,
  hashForSettings,
  hashForSummary,
  hashForWorklog,
  parseHash,
  parseWorkDate,
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

describe('parseWorkDate', () => {
  it('接受合法的 YYYY-MM-DD', () => {
    expect(parseWorkDate('2026-09-11')).toBe('2026-09-11')
  })

  it('擋掉「形狀對但那天不存在」的日期', () => {
    // 這是這個函式存在的唯一理由：`new Date('2026-02-30')` 不會報錯，
    // 它會自己進位成 3/2。網址上一個錯字就變成另一天的工時，而工時會進
    // 月結、事後要走 correction-request 才改得動。
    expect(parseWorkDate('2026-02-30')).toBeNull()
    expect(parseWorkDate('2026-13-01')).toBeNull()
    expect(parseWorkDate('2026-00-10')).toBeNull()
    expect(parseWorkDate('2026-09-00')).toBeNull()
    expect(parseWorkDate('2026-09-31')).toBeNull()
  })

  it('閏年的 2/29 放行，平年的擋掉', () => {
    expect(parseWorkDate('2024-02-29')).toBe('2024-02-29')
    expect(parseWorkDate('2026-02-29')).toBeNull()
  })

  it('形狀不對一律 null', () => {
    for (const bad of ['', '2026-9-11', '20260911', '2026/09/11', 'today', null, undefined]) {
      expect(parseWorkDate(bad)).toBeNull()
    }
  })
})

describe('hashForWorklog', () => {
  it('沒給日期就是週檢視', () => {
    expect(hashForWorklog()).toBe('#/worklog')
    expect(hashForWorklog(null)).toBe('#/worklog')
  })

  it('給了日期就帶上', () => {
    expect(hashForWorklog('2026-09-11')).toBe('#/worklog/2026-09-11')
  })

  it('不合法的日期退回週檢視，不產生指向不存在日期的連結', () => {
    expect(hashForWorklog('2026-02-30')).toBe('#/worklog')
  })
})

describe('parseHash：工時', () => {
  it('#/worklog 是週檢視，沒有指定哪一天', () => {
    const route = parseHash('#/worklog')
    expect(route.section).toBe('worklog')
    expect(route.worklogDate).toBeNull()
  })

  it('#/worklog/:date 選中那一天', () => {
    expect(parseHash('#/worklog/2026-09-11').worklogDate).toBe('2026-09-11')
  })

  it('日期不合法時留在工時模組但不選日期——**不猜成今天**', () => {
    const route = parseHash('#/worklog/2026-02-30')
    expect(route.section).toBe('worklog')
    expect(route.worklogDate).toBeNull()
  })

  it('與 hashForWorklog 來回轉換', () => {
    expect(parseHash(hashForWorklog('2026-09-11')).worklogDate).toBe('2026-09-11')
  })
})

describe('hasDetail', () => {
  it('每個工作台看的是自己的欄位', () => {
    expect(hasDetail({ ...DEFAULT_ROUTE, section: 'summary', spaceId: 'spaces/A' })).toBe(true)
    expect(hasDetail({ ...DEFAULT_ROUTE, section: 'mentions', mentionId: 7 })).toBe(true)
    expect(hasDetail({ ...DEFAULT_ROUTE, section: 'worklog', worklogDate: '2026-09-11' })).toBe(
      true,
    )
  })

  it('工時模組不會去看 spaceId', () => {
    // 這條守的正是「三元式落到 summary 那一支」的退化：`route.spaceId` 在
    // 工時模組永遠是 null，一旦退化，768–1024 的主從切換就永遠回不到工作區
    expect(hasDetail({ ...DEFAULT_ROUTE, section: 'worklog', spaceId: 'spaces/A' })).toBe(false)
  })

  it('設定是覆蓋層，沒有主從之分', () => {
    expect(hasDetail({ ...DEFAULT_ROUTE, section: 'settings', settingsTab: 'reply' })).toBe(false)
  })
})

describe('sameRoute：工時', () => {
  it('日期不同就是不同位置', () => {
    const a = { ...DEFAULT_ROUTE, section: 'worklog' as const, worklogDate: '2026-09-11' }
    const b = { ...DEFAULT_ROUTE, section: 'worklog' as const, worklogDate: '2026-09-12' }
    expect(sameRoute(a, b)).toBe(false)
    expect(sameRoute(a, { ...a })).toBe(true)
  })
})
