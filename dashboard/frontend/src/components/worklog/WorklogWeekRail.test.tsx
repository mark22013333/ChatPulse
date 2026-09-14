import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WorklogWeekRail } from './WorklogWeekRail'
import { RouterProvider } from '@/router/useRouter'
import { useWorklogStore } from '@/store/worklog'
import type { WorklogWeekDay, WorklogWeekDraft, WorklogWeekResponse } from '@/lib/types'

/**
 * 工時草稿的週欄。
 *
 * 這一欄回答「這一週哪幾天還沒交代」，所以斷言集中在**看錯了會做錯事**的幾件：
 *
 * 1. 七天一定是七列（沒草稿的那天也要在），少一列＝那天從此不會有人想起來。
 * 2. 狀態字符要分得出「已送出」與「有幾筆沒送成功」。
 * 3. 標題那一週讀的是**回應**的 `week_start`，不是我們送出去的參數。
 * 4. ZPlanner 不可用時本地草稿照常顯示，而且原因要寫在畫面上。
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

/** 09/07（一）～09/13（日）七天。預設全部沒有草稿。 */
function sevenDays(patch: Record<string, Partial<WorklogWeekDay>> = {}): WorklogWeekDay[] {
  const dates = [
    '2026-09-07',
    '2026-09-08',
    '2026-09-09',
    '2026-09-10',
    '2026-09-11',
    '2026-09-12',
    '2026-09-13',
  ]
  return dates.map((work_date, index) => ({
    work_date,
    weekday: index + 1,
    draft: null,
    zplanner: null,
    ...(patch[work_date] ?? {}),
  }))
}

function weekResponse(over: Partial<WorklogWeekResponse> = {}): WorklogWeekResponse {
  return {
    week_start: '2026-09-07',
    week_end: '2026-09-13',
    days: sevenDays(),
    totals: { draft_hours: '0.00', days_with_draft: 0, days_submitted: 0, entries_unassigned: 0 },
    zplanner: { available: true, total_hours: '16.00', worklog_count: 5 },
    ...over,
  }
}

/** 只 seed 狀態，`loadWeek` 換成 no-op，測試不打 API。 */
function seed(over: Record<string, unknown> = {}) {
  useWorklogStore.setState({
    selectedDate: null,
    weekStart: '2026-09-07',
    week: weekResponse(),
    weekLoading: false,
    weekError: null,
    loadWeek: async () => {},
    ...over,
  })
}

function mount(active = true) {
  return render(
    <RouterProvider>
      <WorklogWeekRail active={active} />
    </RouterProvider>,
  )
}

/** 某一天那一列（整列是一顆按鈕）。 */
function row(label: string): HTMLElement {
  return screen.getByText(label).closest('button') as HTMLElement
}

beforeEach(() => {
  window.location.hash = '#/worklog'
  seed()
})

describe('週欄：七天列', () => {
  it('**七天全部渲染，沒有草稿的那天也在**', () => {
    mount()
    for (const label of ['週一', '週二', '週三', '週四', '週五', '週六', '週日']) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
    expect(screen.getByText('09/07')).toBeInTheDocument()
    expect(screen.getByText('09/13')).toBeInTheDocument()
  })

  it('**weekday 走對照表**：後端回 ISO 星期，1 對到週一、7 對到週日', () => {
    seed({
      week: weekResponse({
        days: sevenDays().map((d) => (d.work_date === '2026-09-13' ? { ...d, weekday: 7 } : d)),
      }),
    })
    mount()
    expect(within(row('週日')).getByText('09/13')).toBeInTheDocument()
  })

  it('沒有草稿那天的時數是 — 並寫明「未產生」', () => {
    mount()
    const monday = row('週一')
    // 一列只有一個 `—`：缺值的那個在時數欄，狀態欄留空（見 WeekDayRow 的註解）
    expect(within(monday).getByText('—')).toBeInTheDocument()
    expect(within(monday).getByText('未產生')).toBeInTheDocument()
  })

  it('**0 小時的那天顯示 0.0 而不是 —**，整列也不會消失', () => {
    seed({
      week: weekResponse({
        days: sevenDays({
          '2026-09-09': { draft: draft({ work_date: '2026-09-09', hours: '0.00' }) },
        }),
      }),
    })
    mount()
    const wed = row('週三')
    expect(within(wed).getByText('0.0')).toBeInTheDocument()
    expect(within(wed).getByText('有草稿未送')).toBeInTheDocument()
  })

  it('時數砍成一位小數靠右顯示', () => {
    seed({
      week: weekResponse({
        days: sevenDays({ '2026-09-08': { draft: draft({ hours: '2.50' }) } }),
      }),
    })
    mount()
    expect(within(row('週二')).getByText('2.5')).toBeInTheDocument()
  })
})

describe('週欄：狀態字符', () => {
  it('已送出是 ✓', () => {
    seed({
      week: weekResponse({
        days: sevenDays({
          '2026-09-07': { draft: draft({ status: 'submitted', submitted_count: 3 }) },
        }),
      }),
    })
    mount()
    const monday = row('週一')
    expect(within(monday).getByText('✓')).toBeInTheDocument()
    expect(within(monday).getByText('已送出')).toBeInTheDocument()
  })

  it('有草稿未送是 ▸', () => {
    seed({
      week: weekResponse({ days: sevenDays({ '2026-09-07': { draft: draft() } }) }),
    })
    mount()
    expect(within(row('週一')).getByText('▸')).toBeInTheDocument()
  })

  it('**部分失敗是 †，不可以畫成 ✓**', () => {
    seed({
      week: weekResponse({
        days: sevenDays({
          '2026-09-07': {
            draft: draft({ status: 'partial', submitted_count: 2, failed_count: 1 }),
          },
        }),
      }),
    })
    mount()
    const monday = row('週一')
    expect(within(monday).getByText('†')).toBeInTheDocument()
    expect(within(monday).getByText('部分未完成')).toBeInTheDocument()
    expect(within(monday).queryByText('✓')).toBeNull()
  })

  it('未產生的缺值是 —，第二重編碼是點線規線（不靠顏色）', () => {
    mount()
    const sunday = row('週日')
    expect(within(sunday).getByText('—')).toBeInTheDocument()
    expect(sunday.className).toContain('border-dotted')
  })

  it('部分失敗的規線是虛線、已送出與有草稿是實線', () => {
    seed({
      week: weekResponse({
        days: sevenDays({
          '2026-09-07': { draft: draft({ status: 'partial', failed_count: 1 }) },
          '2026-09-08': { draft: draft({ status: 'submitted', submitted_count: 1 }) },
        }),
      }),
    })
    mount()
    expect(row('週一').className).toContain('border-dashed')
    expect(row('週二').className).toContain('border-solid')
  })

  it('**還有幾筆沒選專案要寫在那一列上**（未選擋住送出）', () => {
    seed({
      week: weekResponse({
        days: sevenDays({ '2026-09-10': { draft: draft({ unassigned_count: 2 }) } }),
      }),
    })
    mount()
    expect(within(row('週四')).getByText('還有 2 筆沒選專案')).toBeInTheDocument()
  })
})

describe('週欄：導航', () => {
  it('**點某一天導到 #/worklog/YYYY-MM-DD**', async () => {
    mount()
    await userEvent.click(row('週四'))
    expect(window.location.hash).toBe('#/worklog/2026-09-10')
  })

  it('沒有草稿的那天照樣點得進去（那正是要去產草稿的日子）', async () => {
    mount()
    await userEvent.click(row('週日'))
    expect(window.location.hash).toBe('#/worklog/2026-09-13')
  })

  it('選中的那天標 aria-current="date"', () => {
    seed({ selectedDate: '2026-09-11' })
    mount()
    expect(row('週五')).toHaveAttribute('aria-current', 'date')
    expect(row('週一')).not.toHaveAttribute('aria-current')
  })

  it('上一週／下一週按鈕會改 weekStart', async () => {
    mount()
    await userEvent.click(screen.getByRole('button', { name: '上一週' }))
    expect(useWorklogStore.getState().weekStart).toBe('2026-08-31')

    await userEvent.click(screen.getByRole('button', { name: '下一週' }))
    expect(useWorklogStore.getState().weekStart).toBe('2026-09-07')
  })
})

describe('週欄：這是哪一週', () => {
  it('**標題讀回應的 week_start／week_end，不是送出去的參數**', () => {
    // ZPlanner 會把日期正規化到那一週的週一：送週二進去，回的是週一。
    // 拿參數當答案會顯示 09/08–09/14，與下面七列的日期對不起來。
    seed({
      weekStart: '2026-09-08',
      week: weekResponse({ week_start: '2026-09-07', week_end: '2026-09-13' }),
    })
    mount()
    expect(screen.getByRole('heading')).toHaveTextContent('09/07–09/13')
    expect(screen.queryByText(/09\/08–09\/14/)).toBeNull()
  })

  it('還沒拿到回應時不顯示任何日期範圍（不猜）', () => {
    seed({ week: null, weekLoading: true })
    mount()
    expect(screen.getByRole('heading')).not.toHaveTextContent('09/')
  })
})

describe('週欄：載入與失敗', () => {
  it('載入中且還沒有資料時顯示載入狀態', () => {
    seed({ week: null, weekLoading: true })
    mount()
    expect(screen.getByText('載入中…')).toBeInTheDocument()
  })

  it('載入失敗走 role="alert"，並給得出重試', async () => {
    const loadWeek = vi.fn(async () => {})
    seed({ week: null, weekLoading: false, weekError: '無法連線到伺服器', loadWeek })
    mount()

    expect(screen.getByRole('alert')).toHaveTextContent('無法連線到伺服器')
    loadWeek.mockClear()
    await userEvent.click(screen.getByRole('button', { name: '重試' }))
    expect(loadWeek).toHaveBeenCalled()
  })

  it('**重新載入失敗時，已經在畫面上的七天不會被清掉**', () => {
    seed({ weekError: '無法連線到伺服器' })
    mount()
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText('週一')).toBeInTheDocument()
    expect(screen.getByText('週日')).toBeInTheDocument()
  })
})

describe('週欄：週合計', () => {
  it('列出本週草稿時數與已送出天數', () => {
    seed({
      week: weekResponse({
        totals: {
          draft_hours: '15.50',
          days_with_draft: 5,
          days_submitted: 3,
          entries_unassigned: 0,
        },
      }),
    })
    mount()
    expect(screen.getByText('本週草稿')).toBeInTheDocument()
    expect(screen.getByText('15.5')).toBeInTheDocument()
    expect(screen.getByText('已送出')).toBeInTheDocument()
    expect(screen.getByText('3 / 7')).toBeInTheDocument()
  })

  it('有筆數沒選專案時列出來；為 0 時整列收起來', () => {
    seed({
      week: weekResponse({
        totals: {
          draft_hours: '8.00',
          days_with_draft: 1,
          days_submitted: 0,
          entries_unassigned: 3,
        },
      }),
    })
    mount()
    expect(screen.getByText('未選專案')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()

    seed()
    mount()
    expect(screen.queryByText('未選專案')).toBeNull()
  })
})

describe('週欄：ZPlanner 不可用', () => {
  const unavailable = {
    week: weekResponse({
      zplanner: { available: false, reason: '未設定 ZPLANNER_BASE_URL' },
      days: sevenDays({ '2026-09-07': { draft: draft({ hours: '2.00' }) } }),
      totals: {
        draft_hours: '2.00',
        days_with_draft: 1,
        days_submitted: 0,
        entries_unassigned: 0,
      },
    }),
  }

  it('**本地草稿照常顯示**——ZPlanner 不可用不是載入失敗', () => {
    seed(unavailable)
    mount()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(within(row('週一')).getByText('2.0')).toBeInTheDocument()
    expect(screen.getByText('本週草稿')).toBeInTheDocument()
  })

  it('**原因要用可見文字寫出來，不可以靜默隱藏**', () => {
    seed(unavailable)
    mount()
    expect(screen.getByText(/未設定 ZPLANNER_BASE_URL/)).toHaveTextContent(
      /本地草稿不受影響，照常顯示/,
    )
  })

  it('可用時列出已填進 ZPlanner 的時數', () => {
    seed()
    mount()
    expect(screen.getByText('已填 ZPlanner')).toBeInTheDocument()
    expect(screen.getByText('16.0')).toBeInTheDocument()
  })

  it('**某天 ZPlanner 已經有工時就寫在那一列**（不然會被重複填一次）', () => {
    seed({
      week: weekResponse({
        days: sevenDays({
          '2026-09-08': { zplanner: { hours: '8.00', worklog_count: 2 } },
        }),
      }),
    })
    mount()
    const tue = row('週二')
    // 本地沒有草稿（—），但 ZPlanner 那邊已經有 8 小時
    expect(within(tue).getByText('未產生')).toBeInTheDocument()
    expect(within(tue).getByText(/ZPlanner 已填/)).toHaveTextContent('8.0')
  })
})

describe('週欄：設計紅線', () => {
  it('**渲染結果裡 [title] 選得到 0 個**（設計規格 §10.3）', () => {
    seed({
      week: weekResponse({
        zplanner: { available: false, reason: '未設定 ZPLANNER_BASE_URL' },
        days: sevenDays({
          '2026-09-07': { draft: draft({ status: 'partial', failed_count: 1 }) },
          '2026-09-08': {
            draft: draft({ unassigned_count: 2 }),
            zplanner: { hours: '8.00', worklog_count: 2 },
          },
        }),
        totals: {
          draft_hours: '10.00',
          days_with_draft: 2,
          days_submitted: 0,
          entries_unassigned: 2,
        },
      }),
    })
    const { container } = mount()
    expect(container.querySelectorAll('[title]')).toHaveLength(0)
  })

  it('七列共用同一組欄寬（右緣對齊靠的是同一個 grid template）', () => {
    seed({
      week: weekResponse({ days: sevenDays({ '2026-09-07': { draft: draft() } }) }),
    })
    const { container } = mount()
    const grids = container.querySelectorAll('.grid-cols-\\[1fr_var\\(--container-metric\\)_1\\.75rem\\]')
    // 七天列 ＋ 週合計那幾列，全部用同一組欄寬
    expect(grids.length).toBeGreaterThanOrEqual(7)
  })
})

describe('只在這個工作台真的在看的時候才載入', () => {
  it('**active=false 時完全不發請求**', async () => {
    // `Pane` 刻意不卸載子元件（三個工作台常駐掛載，見 AppShell），所以少了
    // 這道閘，光是打開 app 就會對 ZPlanner 發一次請求——即使使用者從頭到尾
    // 沒點進工時。這條守的就是那件事。
    const loadWeek = vi.fn(async () => {})
    seed({ loadWeek })
    mount(false)
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(loadWeek).not.toHaveBeenCalled()
  })

  it('active=true 時載入一次', async () => {
    const loadWeek = vi.fn(async () => {})
    seed({ loadWeek })
    mount(true)
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(loadWeek).toHaveBeenCalledTimes(1)
  })
})
