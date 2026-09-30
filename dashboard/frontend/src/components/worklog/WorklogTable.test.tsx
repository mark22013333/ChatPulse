import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WorklogTable } from './WorklogTable'
import { api } from '@/lib/api'
import { RouterProvider } from '@/router/useRouter'
import { useAuthStore } from '@/store/auth'
import { resetPageSizeForTest } from '@/store/pageSize'
import { useWorklogStore } from '@/store/worklog'
import type { Me, WorklogWeekDay, WorklogWeekDraft, WorklogWeekResponse } from '@/lib/types'

/**
 * 工時草稿的整頁表格（2026-10-01 改版 §4.4）。
 *
 * 這一份守「錯了畫面看不出來」的幾件事：
 *
 * 1. **非工時視圖不發請求**（`active` 閘門）——用真的 store `loadWeek` ＋ api spy，
 *    不是把 loadWeek 換成 no-op，才驗得到「真的沒有打出去」。
 * 2. **專案分配不顯示系統建議**（ADR-0008）——資料裡刻意塞了候選專案當正對照。
 * 3. **切 tab 的瞬間不拿上一週的資料畫這一週**。
 * 4. **「未送出」兩週缺一週時不畫半份**。
 * 5. 排序對全部列做完再切頁（第二頁才有的那一天當正對照）。
 */

const THIS = '2026-09-07'
const LAST = '2026-08-31'
const VIEWER = 41

function draft(over: Partial<WorklogWeekDraft> = {}): WorklogWeekDraft {
  return {
    id: 1,
    work_date: THIS,
    status: 'draft',
    total_hours: '8.00',
    tz_offset: '+08:00',
    entry_count: 3,
    hours: '8.00',
    allocated_hours: '8.00',
    submitted_count: 0,
    failed_count: 0,
    unknown_count: 0,
    unassigned_count: 3,
    ...over,
  }
}

function isoPlus(start: string, n: number): string {
  const [y, m, d] = start.split('-').map(Number)
  const dt = new Date(y, m - 1, d + n)
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`
}

function week(start: string, patch: Record<number, Partial<WorklogWeekDay>> = {}): WorklogWeekResponse {
  const days: WorklogWeekDay[] = Array.from({ length: 7 }, (_, i) => ({
    work_date: isoPlus(start, i),
    weekday: i + 1,
    draft: null,
    zplanner: null,
    ...(patch[i] ?? {}),
  }))
  return {
    week_start: start,
    week_end: isoPlus(start, 6),
    days,
    totals: { draft_hours: '0.00', days_with_draft: 0, days_submitted: 0, entries_unassigned: 0 },
    zplanner: { available: true, total_hours: '0.00', worklog_count: 0 },
  }
}

function mount(active = true) {
  return render(
    <RouterProvider>
      <WorklogTable active={active} thisWeek={THIS} />
    </RouterProvider>,
  )
}

const table = () => screen.getByRole('table', { name: '工時草稿清單' })
const bodyRows = () => within(table()).getAllByRole('row').slice(1)

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0))
  })
}

beforeEach(() => {
  try {
    localStorage.clear()
  } catch {
    // jsdom 一定有
  }
  resetPageSizeForTest()
  useAuthStore.setState({ me: { viewer: { id: VIEWER } } as unknown as Me })
  window.location.hash = '#/worklog'
  // 真的 store：loadWeek 會打 api.worklogWeek（由各條測試 spy）
  useWorklogStore.getState().reset()
  useWorklogStore.setState({ weekStart: THIS })
})

afterEach(() => {
  vi.restoreAllMocks()
  useAuthStore.setState({ me: null })
})

describe('🔴 active 閘門：非工時視圖時不對 ZPlanner 發請求', () => {
  it('active=false：一次都不打（本週、上週都不打）', async () => {
    const spy = vi.spyOn(api, 'worklogWeek').mockResolvedValue(week(THIS))
    mount(false)
    await flush()
    expect(spy).not.toHaveBeenCalled()
  })

  it('正對照：active=true 打一次，參數是本週', async () => {
    const spy = vi.spyOn(api, 'worklogWeek').mockResolvedValue(week(THIS))
    mount(true)
    await flush()
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy).toHaveBeenCalledWith(THIS)
  })

  it('切成不在看（active 由 true → false）之後換 tab 也不打；回來才打', async () => {
    const spy = vi.spyOn(api, 'worklogWeek').mockImplementation(async (s: string) => week(s))
    const view = mount(true)
    await flush()
    expect(spy).toHaveBeenCalledTimes(1)
    view.rerender(
      <RouterProvider>
        <WorklogTable active={false} thisWeek={THIS} />
      </RouterProvider>,
    )
    // 表格收起來時 tab 仍可被程式改（例：測試直接點）；重點是不打 API
    await userEvent.click(screen.getByRole('tab', { name: '未送出' }))
    await flush()
    expect(spy).toHaveBeenCalledTimes(1)
    view.rerender(
      <RouterProvider>
        <WorklogTable active thisWeek={THIS} />
      </RouterProvider>,
    )
    await flush()
    // 回來後補打上週（未送出要兩週）
    expect(spy).toHaveBeenCalledWith(LAST)
  })
})

describe('一列一天', () => {
  it('本週七列、欄位齊全；「共 N 天」', async () => {
    vi.spyOn(api, 'worklogWeek').mockResolvedValue(week(THIS))
    mount()
    await waitFor(() => expect(bodyRows()).toHaveLength(7))
    for (const name of ['日期', '星期', '草稿時數', '專案分配', '狀態', '燈號']) {
      expect(screen.getByRole('columnheader', { name: new RegExp(name) })).toBeInTheDocument()
    }
    expect(screen.getByText('週一')).toBeInTheDocument()
    expect(screen.getByText('09/07')).toBeInTheDocument()
    expect(screen.getByText(/共/)).toHaveTextContent('共 7 天')
  })

  it('狀態分得出未產生／草稿／部分未完成／已送出，燈號有讀得出來的文字', async () => {
    vi.spyOn(api, 'worklogWeek').mockResolvedValue(
      week(THIS, {
        0: { draft: draft({ status: 'submitted', unassigned_count: 0 }) },
        1: { draft: draft() },
        2: { draft: draft({ status: 'submitted', failed_count: 1, unassigned_count: 0 }) },
      }),
    )
    mount()
    await waitFor(() => expect(bodyRows()).toHaveLength(7))
    const rows = bodyRows()
    expect(within(rows[0]).getByText('已送出')).toBeInTheDocument()
    expect(within(rows[0]).getByText('完整')).toBeInTheDocument()
    expect(within(rows[1]).getByText('草稿')).toBeInTheDocument()
    expect(within(rows[1]).getByText('待確認')).toBeInTheDocument()
    // 送出時有一筆失敗：不可以畫成「已送出」
    expect(within(rows[2]).getByText('部分未完成')).toBeInTheDocument()
    expect(within(rows[3]).getByText('未產生')).toBeInTheDocument()
    expect(within(rows[3]).getByText('尚未開始')).toBeInTheDocument()
  })

  it('ZPlanner 已填的那天寫出來；ZPlanner 不可用時原因寫在畫面上', async () => {
    const w = week(THIS, { 0: { zplanner: { hours: '3.00', worklog_count: 1 } } })
    vi.spyOn(api, 'worklogWeek').mockResolvedValueOnce(w)
    const view = mount()
    await waitFor(() => expect(within(bodyRows()[0]).getByText(/ZPlanner 已填/)).toHaveTextContent('3.0'))
    view.unmount()

    vi.spyOn(api, 'worklogWeek').mockResolvedValue({
      ...week(THIS),
      zplanner: { available: false, reason: '還沒設定 ZPlanner 的連線。' },
    })
    useWorklogStore.getState().reset()
    useWorklogStore.setState({ weekStart: THIS })
    mount()
    await waitFor(() => expect(screen.getByText(/還沒設定 ZPlanner 的連線/)).toBeInTheDocument())
    expect(screen.getByText(/本地草稿不受影響/)).toBeInTheDocument()
    expect(bodyRows()).toHaveLength(7)
  })

  it('點一列 → 導覽到那一天', async () => {
    vi.spyOn(api, 'worklogWeek').mockResolvedValue(week(THIS))
    mount()
    await waitFor(() => expect(bodyRows()).toHaveLength(7))
    await userEvent.click(within(bodyRows()[2]).getByText('09/09'))
    expect(window.location.hash).toBe('#/worklog/2026-09-09')
  })
})

describe('🔴 專案分配：只顯示 Viewer 已選定的歸屬（ADR-0008）', () => {
  const suggested = {
    clusters: [
      {
        key: 'c1',
        candidates: [
          { issue_key: 'SUG-7', project_key: 'SUGGESTED-PRJ', title: '系統建議的訂閱專案', confidence: 'strong' },
        ],
      },
    ],
  }

  it('資料裡帶著系統建議的專案（正對照），畫面只寫「歸屬尚未選擇」', async () => {
    const w = week(THIS, {
      0: { draft: draft({ generation_meta: suggested }) },
      1: { draft: draft({ unassigned_count: 1, generation_meta: suggested }) },
    })
    // 正對照前提：這份回應真的帶著建議專案
    expect(JSON.stringify(w)).toContain('SUGGESTED-PRJ')
    vi.spyOn(api, 'worklogWeek').mockResolvedValue(w)
    mount()
    await waitFor(() => expect(bodyRows()).toHaveLength(7))

    expect(within(bodyRows()[0]).getByText('歸屬尚未選擇（3 段）')).toBeInTheDocument()
    expect(within(bodyRows()[1]).getByText('已選 2 段・歸屬尚未選擇 1 段')).toBeInTheDocument()
    const text = document.body.textContent ?? ''
    expect(text).not.toContain('SUGGESTED-PRJ')
    expect(text).not.toContain('SUG-7')
    expect(text).not.toContain('系統建議的訂閱專案')
  })
})

describe('tabs', () => {
  it('上週：打上週；**切過去的瞬間不拿本週的七列冒充上週**', async () => {
    let release: (w: WorklogWeekResponse) => void = () => {}
    vi.spyOn(api, 'worklogWeek').mockImplementation((s: string) =>
      s === THIS ? Promise.resolve(week(THIS)) : new Promise((r) => (release = r)),
    )
    mount()
    await waitFor(() => expect(bodyRows()).toHaveLength(7))

    await userEvent.click(screen.getByRole('tab', { name: '上週' }))
    await flush()
    // 上週還在路上：表格是「載入中」，不是本週的 09/07
    expect(screen.queryByText('09/07')).toBeNull()
    expect(screen.getByText('載入中…')).toBeInTheDocument()

    await act(async () => release(week(LAST)))
    await waitFor(() => expect(screen.getByText('08/31')).toBeInTheDocument())
    expect(bodyRows()).toHaveLength(7)
  })

  it('未送出：本週＋上週裡有草稿但沒送完的天；範圍寫在畫面上', async () => {
    vi.spyOn(api, 'worklogWeek').mockImplementation(async (s: string) =>
      s === THIS
        ? week(THIS, {
            0: { draft: draft({ status: 'submitted', unassigned_count: 0 }) },
            1: { draft: draft() },
          })
        : week(LAST, { 4: { draft: draft({ status: 'partial' }) } }),
    )
    mount()
    await waitFor(() => expect(bodyRows()).toHaveLength(7))
    await userEvent.click(screen.getByRole('tab', { name: '未送出' }))

    await waitFor(() => expect(bodyRows()).toHaveLength(2))
    // 上週五（09/04）的部分未完成、本週二（09/08）的草稿；已送出與未產生都不在
    expect(within(bodyRows()[0]).getByText('09/04')).toBeInTheDocument()
    expect(within(bodyRows()[1]).getByText('09/08')).toBeInTheDocument()
    expect(screen.getByText(/範圍：上週與本週/)).toHaveTextContent('更早的未送出草稿不在這裡')
  })

  it('🔴 未送出：上週載入失敗時不畫半份（只剩本週會讓人以為上週都交代完了）', async () => {
    vi.spyOn(api, 'worklogWeek').mockImplementation(async (s: string) => {
      if (s === LAST) throw new Error('無法連線到伺服器')
      return week(THIS, { 1: { draft: draft() } })
    })
    mount()
    await waitFor(() => expect(bodyRows()).toHaveLength(7))
    await userEvent.click(screen.getByRole('tab', { name: '未送出' }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('上週的資料載入失敗'))
    expect(screen.queryByText('09/08')).toBeNull()
    expect(screen.getByRole('button', { name: '重試' })).toBeInTheDocument()
  })

  it('回應的週次與要的不符：照實說，不畫錯位的日期', async () => {
    vi.spyOn(api, 'worklogWeek').mockResolvedValue(week('2026-09-06'))
    mount()
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('伺服器回的卻是 2026-09-06'))
    expect(screen.queryByText('09/06')).toBeNull()
  })
})

describe('翻週：可以翻到任何一週（與舊週欄同一套行為）', () => {
  it('翻到兩週前：打的是那一週的 API、畫的是那一週；tab 一顆都不亮；「回到本週」回得來', async () => {
    const spy = vi.spyOn(api, 'worklogWeek').mockImplementation(async (s: string) => week(s))
    mount()
    await waitFor(() => expect(bodyRows()).toHaveLength(7))
    expect(screen.getByRole('tab', { name: '本週' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByRole('button', { name: '回到本週' })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: '上一週' }))
    // 上一週＝上週：tab 捷徑跟著亮
    await waitFor(() => expect(screen.getByText('08/31')).toBeInTheDocument())
    expect(screen.getByRole('tab', { name: '上週' })).toHaveAttribute('aria-selected', 'true')

    await userEvent.click(screen.getByRole('button', { name: '上一週' }))
    await waitFor(() => expect(screen.getByText('08/24')).toBeInTheDocument())
    expect(spy).toHaveBeenLastCalledWith('2026-08-24')
    expect(bodyRows()).toHaveLength(7)
    expect(screen.getByRole('heading', { name: '週次' })).toHaveTextContent('08/24–08/30')
    // 🔴 不在本週也不在上週：沒有任何一顆 tab 宣稱自己是目前的視圖
    for (const name of ['本週', '上週', '未送出']) {
      expect(screen.getByRole('tab', { name })).not.toHaveAttribute('aria-selected', 'true')
    }

    await userEvent.click(screen.getByRole('button', { name: '下一週' }))
    await waitFor(() => expect(screen.getByText('08/31')).toBeInTheDocument())
    expect(spy).toHaveBeenLastCalledWith(LAST)

    await userEvent.click(screen.getByRole('button', { name: '上一週' }))
    await waitFor(() => expect(screen.getByText('08/24')).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: '回到本週' }))
    await waitFor(() => expect(screen.getByText('09/07')).toBeInTheDocument())
    expect(screen.getByRole('tab', { name: '本週' })).toHaveAttribute('aria-selected', 'true')
  })

  it('🔴 翻到更早的週，回應的 week_start 對不上 → 不畫、照實說', async () => {
    vi.spyOn(api, 'worklogWeek').mockImplementation(async (s: string) =>
      // 更早那一週的回應起點錯開一天（例如後端改成週日起算）
      s === '2026-08-24' ? week('2026-08-23') : week(s),
    )
    mount()
    await waitFor(() => expect(bodyRows()).toHaveLength(7))
    await userEvent.click(screen.getByRole('button', { name: '上一週' }))
    await userEvent.click(screen.getByRole('button', { name: '上一週' }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('伺服器回的卻是 2026-08-23'))
    expect(screen.queryByText('08/23')).toBeNull()
    expect(screen.queryByText('08/31')).toBeNull()
    // 標題也不顯示錯位的範圍
    expect(screen.getByRole('heading', { name: '週次' })).not.toHaveTextContent('08/23')
  })

  it('未送出模式不顯示翻週（它的範圍固定是本週＋上週，寫在畫面上）', async () => {
    vi.spyOn(api, 'worklogWeek').mockImplementation(async (s: string) => week(s))
    mount()
    await waitFor(() => expect(bodyRows()).toHaveLength(7))
    await userEvent.click(screen.getByRole('button', { name: '上一週' }))
    await userEvent.click(screen.getByRole('button', { name: '上一週' }))
    await userEvent.click(screen.getByRole('tab', { name: '未送出' }))
    expect(screen.queryByRole('button', { name: '上一週' })).toBeNull()
    await waitFor(() => expect(screen.getByText(/範圍：上週與本週/)).toBeInTheDocument())
    // 進未送出時主要那一週回到本週
    expect(useWorklogStore.getState().weekStart).toBe(THIS)
  })
})

describe('排序、分頁：對全部列做完再切頁', () => {
  it('🔴 每頁 10 筆、未送出 14 天，時數最多的那天本來在第二頁；依時數排後在第一頁第一列', async () => {
    localStorage.setItem(`chatpulse.pageSize.${VIEWER}`, JSON.stringify({ worklog: 10 }))
    resetPageSizeForTest()
    const all = (start: string, big = -1) =>
      week(
        start,
        Object.fromEntries(
          Array.from({ length: 7 }, (_, i) => [
            i,
            { draft: draft({ work_date: isoPlus(start, i), hours: i === big ? '9.50' : '4.00' }) },
          ]),
        ),
      )
    // 最多的那天是本週日（09/13），依日期排是第 14 列
    vi.spyOn(api, 'worklogWeek').mockImplementation(async (s: string) => (s === THIS ? all(THIS, 6) : all(LAST)))
    mount()
    await waitFor(() => expect(bodyRows()).toHaveLength(7))
    await userEvent.click(screen.getByRole('tab', { name: '未送出' }))
    await waitFor(() => expect(bodyRows()).toHaveLength(10))

    // 正對照：依日期排時第一頁沒有 09/13
    expect(within(table()).queryByText('09/13')).toBeNull()
    expect(screen.getByText(/共/)).toHaveTextContent('共 14 天')

    await userEvent.click(screen.getByRole('button', { name: /草稿時數/ }))
    expect(within(bodyRows()[0]).getByText('09/13')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /草稿時數/ })).toHaveAttribute('aria-sort', 'descending')
  })
})
