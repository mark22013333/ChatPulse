import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  addDays,
  formatHours,
  isoDate,
  mondayOf,
  remainingHours,
  useWorklogStore,
  weekDayStatus,
} from './worklog'
import { api } from '@/lib/api'
import type { WorklogWeekDay, WorklogWeekDraft, WorklogWeekResponse } from '@/lib/types'

/**
 * 週檢視的純函式與載入流程。
 *
 * 斷言集中在**會安靜出錯的那幾件事**：星期日的週一換算、UTC 少一天、
 * 「有幾筆沒送成功」被畫成「已送出」、以及連按換週時的亂序回應。
 * 這幾樣錯了畫面都還是好好的，只是數字或日期是錯的。
 */

function draft(over: Partial<WorklogWeekDraft> = {}): WorklogWeekDraft {
  return {
    id: 12,
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
    unassigned_count: 0,
    ...over,
  }
}

function day(over: Partial<WorklogWeekDay> = {}): WorklogWeekDay {
  return { work_date: '2026-09-07', weekday: 1, draft: null, zplanner: null, ...over }
}

describe('isoDate：用本地時區的年月日', () => {
  it('**不可以走 UTC**——台北早上八點以前會少一天', () => {
    // 2026-09-07 07:00 (+08:00) 的 UTC 是 2026-09-06T23:00Z。
    // toISOString().slice(0,10) 會給 09-06，而使用者心裡的日期是 09-07。
    const local = new Date(2026, 8, 7, 7, 0, 0)
    expect(isoDate(local)).toBe('2026-09-07')
  })

  it('月與日補零', () => {
    expect(isoDate(new Date(2026, 0, 3))).toBe('2026-01-03')
  })
})

describe('mondayOf：星期日不可以跳到下一週', () => {
  it('星期一回自己', () => {
    expect(mondayOf(new Date(2026, 8, 7))).toBe('2026-09-07')
  })

  it('週間任一天都回同一個星期一', () => {
    expect(mondayOf(new Date(2026, 8, 10))).toBe('2026-09-07')
    expect(mondayOf(new Date(2026, 8, 12))).toBe('2026-09-07')
  })

  it('**星期日回的是同一週的週一，不是隔天**（getDay() 的 0 陷阱）', () => {
    // 2026-09-13 是星期日。用 getDay()（=0）直接減會得到 2026-09-14，
    // 整欄七天全部往後錯一週，而且只有一週裡的一天會出錯。
    expect(mondayOf(new Date(2026, 8, 13))).toBe('2026-09-07')
  })

  it('跨月也對', () => {
    // 2026-10-01 是星期四 → 同週週一是 09-28
    expect(mondayOf(new Date(2026, 9, 1))).toBe('2026-09-28')
  })
})

describe('addDays', () => {
  it('往前七天', () => {
    expect(addDays('2026-09-07', -7)).toBe('2026-08-31')
  })

  it('往後七天', () => {
    expect(addDays('2026-09-07', 7)).toBe('2026-09-14')
  })

  it('跨年', () => {
    expect(addDays('2026-12-28', 7)).toBe('2027-01-04')
  })

  it('閏年的二月底', () => {
    expect(addDays('2028-02-28', 1)).toBe('2028-02-29')
  })
})

describe('formatHours', () => {
  it('兩位小數砍成一位（0.5 的倍數，無損）', () => {
    expect(formatHours('8.00')).toBe('8.0')
    expect(formatHours('2.50')).toBe('2.5')
  })

  it('**零小時是 0.0，不是 —**：那天真的填了零，與「沒有草稿」是兩件事', () => {
    expect(formatHours('0.00')).toBe('0.0')
  })

  it('null／空字串／算不動的值回 —', () => {
    expect(formatHours(null)).toBe('—')
    expect(formatHours(undefined)).toBe('—')
    expect(formatHours('')).toBe('—')
    expect(formatHours('  ')).toBe('—')
    expect(formatHours('欸')).toBe('—')
  })
})

describe('weekDayStatus', () => {
  it('沒有草稿＝absent', () => {
    expect(weekDayStatus(day())).toBe('absent')
  })

  it('有草稿還沒送＝drafted', () => {
    expect(weekDayStatus(day({ draft: draft() }))).toBe('drafted')
  })

  it('整天送完＝submitted', () => {
    expect(weekDayStatus(day({ draft: draft({ status: 'submitted', submitted_count: 3 }) }))).toBe(
      'submitted',
    )
  })

  it('**status 是 partial 就是 partial**，不可以退回 draft 或畫成已送出', () => {
    expect(
      weekDayStatus(day({ draft: draft({ status: 'partial', submitted_count: 2, failed_count: 1 }) })),
    ).toBe('partial')
  })

  it('**有筆數失敗時不可以畫成「已送出」**——那幾筆會沒有人回來補', () => {
    expect(
      weekDayStatus(day({ draft: draft({ status: 'submitted', failed_count: 1 }) })),
    ).toBe('partial')
  })

  it('**逾時（unknown）也算 partial**：逾時不保證沒寫進去，需要人去對帳', () => {
    expect(
      weekDayStatus(day({ draft: draft({ status: 'submitted', unknown_count: 1 }) })),
    ).toBe('partial')
  })

  it('沒認過的 status 退回 drafted（看得到、點得進去），不畫成空白', () => {
    expect(weekDayStatus(day({ draft: draft({ status: 'locked' as never }) }))).toBe('drafted')
  })
})

// ── store ────────────────────────────────────────────────────

function weekResponse(over: Partial<WorklogWeekResponse> = {}): WorklogWeekResponse {
  return {
    week_start: '2026-09-07',
    week_end: '2026-09-13',
    days: [],
    totals: { draft_hours: '0.00', days_with_draft: 0, days_submitted: 0, entries_unassigned: 0 },
    zplanner: { available: false, reason: '未設定 ZPLANNER_BASE_URL' },
    ...over,
  }
}

beforeEach(() => {
  useWorklogStore.setState({
    selectedDate: null,
    weekStart: '2026-09-07',
    week: null,
    weekLoading: false,
    weekError: null,
  })
  vi.restoreAllMocks()
})

describe('worklog store：換週', () => {
  it('shiftWeek(-1) 退一週、shiftWeek(1) 進一週', () => {
    useWorklogStore.getState().shiftWeek(-1)
    expect(useWorklogStore.getState().weekStart).toBe('2026-08-31')
    useWorklogStore.getState().shiftWeek(1)
    expect(useWorklogStore.getState().weekStart).toBe('2026-09-07')
  })

  it('setWeekStart 直接指定', () => {
    useWorklogStore.getState().setWeekStart('2026-01-05')
    expect(useWorklogStore.getState().weekStart).toBe('2026-01-05')
  })
})

describe('worklog store：loadWeek', () => {
  it('帶著目前的 weekStart 去打端點，回應放進 week', async () => {
    const spy = vi.spyOn(api, 'worklogWeek').mockResolvedValue(weekResponse())
    await useWorklogStore.getState().loadWeek()

    expect(spy).toHaveBeenCalledWith('2026-09-07')
    expect(useWorklogStore.getState().week?.week_start).toBe('2026-09-07')
    expect(useWorklogStore.getState().weekLoading).toBe(false)
    expect(useWorklogStore.getState().weekError).toBeNull()
  })

  it('失敗時把可顯示的訊息放進 weekError，week 維持原樣', async () => {
    vi.spyOn(api, 'worklogWeek').mockRejectedValue(new Error('無法連線到伺服器'))
    await useWorklogStore.getState().loadWeek()

    expect(useWorklogStore.getState().weekError).toBe('無法連線到伺服器')
    expect(useWorklogStore.getState().weekLoading).toBe(false)
  })

  it('**晚到的舊回應不可以蓋掉現在這一週**（連按上一週會亂序）', async () => {
    vi.spyOn(api, 'worklogWeek').mockImplementation(async (weekStart: string) => {
      // 舊的那一週故意回得比較慢
      if (weekStart === '2026-09-07') await new Promise((r) => setTimeout(r, 20))
      return weekResponse({ week_start: weekStart, week_end: addDays(weekStart, 6) })
    })

    const slow = useWorklogStore.getState().loadWeek('2026-09-07')
    useWorklogStore.getState().setWeekStart('2026-08-31')
    await useWorklogStore.getState().loadWeek('2026-08-31')
    await slow

    expect(useWorklogStore.getState().week?.week_start).toBe('2026-08-31')
  })

  it('reset 把週狀態一起清掉', () => {
    useWorklogStore.setState({ week: weekResponse(), selectedDate: '2026-09-09' })
    useWorklogStore.getState().reset()

    expect(useWorklogStore.getState().week).toBeNull()
    expect(useWorklogStore.getState().selectedDate).toBeNull()
    expect(useWorklogStore.getState().weekStart).toBe(mondayOf())
  })
})

describe('remainingHours', () => {
  it('就是目標減已填', () => {
    expect(remainingHours('40.00', '15.50')).toBe('24.5')
  })

  it('超過目標回 0.0，不回負數', () => {
    // 「還差 −3.0 小時」要讓人在腦子裡轉一次才看得懂，
    // 而這一欄的用途正是「還要補多少」
    expect(remainingHours('40.00', '43.00')).toBe('0.0')
  })

  it('剛好達標是 0.0', () => {
    expect(remainingHours('40.00', '40.00')).toBe('0.0')
  })

  it('任一邊算不出數字就回 —', () => {
    for (const [t, f] of [
      [null, '15.5'],
      ['40.0', null],
      [undefined, undefined],
      ['四十', '15.5'],
      ['', '15.5'],
    ] as const) {
      expect(remainingHours(t, f)).toBe('—')
    }
  })
})
