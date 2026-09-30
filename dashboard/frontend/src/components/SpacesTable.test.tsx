import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SpacesTable } from './SpacesTable'
import { RouterProvider } from '@/router/useRouter'
import { useAuthStore } from '@/store/auth'
import { resetListSortForTest, useListSortStore } from '@/store/listSort'
import { useMentionsStore } from '@/store/mentions'
import { resetPageSizeForTest } from '@/store/pageSize'
import { useSpacesStore } from '@/store/spaces'
import type { Me, Mention, MentionStateValue, Space } from '@/lib/types'

/**
 * 摘要工作台的 Space 表格（2026-10-01 改版 §4.2、§4.3）。
 *
 * 最重要的是「第二頁才有的資料」那一組正對照：只排當頁 25 筆的實作在畫面上
 * 看起來完全正常，只有拿「本來排在第 30 位」的那一筆去驗，才分得出排序
 * 是不是對全部 Space 做的。
 */

const VIEWER = 21

function space(n: number, over: Partial<Space> = {}): Space {
  return {
    id: `spaces/s${String(n).padStart(2, '0')}`,
    displayName: `空間 ${String(n).padStart(2, '0')}`,
    type: 'SPACE',
    // 預設全部是 2026-09-01，時間一樣時依名稱排——順序就是 01、02、03…
    lastActiveTime: '2026-09-01T02:00:00Z',
    ...over,
  }
}

function mention(id: number, spaceId: string, state: MentionStateValue = 'pending'): Mention {
  return {
    id,
    space_id: spaceId,
    space_name: spaceId,
    message_name: `m${id}`,
    thread_name: null,
    sender_display: 'x',
    create_time: '2026-09-30T00:00:00Z',
    state,
    resolved_at: null,
  }
}

/** 40 筆，第 30 筆是最新的——它在「後端給的順序」裡落在第二頁。 */
function forty(): Space[] {
  return Array.from({ length: 40 }, (_, i) =>
    i === 29 ? space(i + 1, { lastActiveTime: '2026-09-30T08:00:00Z', displayName: '最新的那個' }) : space(i + 1),
  )
}

const togglePin = vi.fn(async () => {})

function seed(items: Space[], mentions: Partial<ReturnType<typeof useMentionsStore.getState>> = {}) {
  useSpacesStore.setState({
    items,
    total: items.length,
    cached: false,
    cachedAt: null,
    loading: false,
    refreshing: false,
    error: null,
    pinning: false,
    togglePin,
    load: async () => {},
  })
  useMentionsStore.setState({
    items: [],
    counts: { pending: 0, resolved: 0 },
    loading: false,
    error: null,
    ...mentions,
  })
}

function mount() {
  return render(
    <RouterProvider>
      <SpacesTable />
    </RouterProvider>,
  )
}

/** 表格 body 裡每一列的 Space 名稱（第二欄）。 */
function names(): string[] {
  const table = screen.getByRole('table', { name: 'Space 清單' })
  const rows = within(table).getAllByRole('row').slice(1)
  return rows.map((row) => within(row).getAllByRole('cell')[1].textContent ?? '')
}

beforeEach(() => {
  window.history.replaceState(null, '', '#/summary')
  try {
    localStorage.clear()
  } catch {
    // jsdom 一定有；保險起見
  }
  resetListSortForTest()
  resetPageSizeForTest()
  useAuthStore.setState({ me: { viewer: { id: VIEWER } } as unknown as Me })
  togglePin.mockClear()
})

afterEach(() => {
  useAuthStore.setState({ me: null })
})

describe('SpacesTable：分頁、排序、篩選作用於全部 Space', () => {
  it('預設每頁 25 列，「共 N 個 Space」是全部的數量不是當頁', () => {
    seed(forty())
    mount()
    expect(names()).toHaveLength(25)
    expect(screen.getByText(/共/, { selector: '[aria-live]' })).toHaveTextContent('共 40 個 Space')
    expect(screen.getByRole('navigation', { name: 'Space 清單分頁' })).toBeInTheDocument()
  })

  it('🔴 正對照：本來排在第 30 位的最新 Space，依最後活動排序後出現在第一頁第一列', () => {
    seed(forty())
    mount()
    // 預設排序是最後活動新→舊。只排當頁的實作，第一列會是「空間 01」
    expect(names()[0]).toContain('最新的那個')
  })

  it('🔴 正對照：搜尋只在第二頁的名字，找得到，而且總數跟著變', async () => {
    seed(forty())
    mount()
    await userEvent.type(screen.getByRole('textbox', { name: '搜尋 Space 名稱' }), '空間 38')
    expect(names()).toEqual(['空間 38'])
    expect(screen.getByText(/共/, { selector: '[aria-live]' })).toHaveTextContent('共 1 個 Space')
  })

  it('點表頭「Space 名稱」改成依名稱排，寫回 Viewer 的排序偏好', async () => {
    seed(forty())
    mount()
    await userEvent.click(screen.getByRole('button', { name: /Space 名稱/ }))
    expect(names()[0]).toBe('空間 01')
    expect(useListSortStore.getState().byViewer[VIEWER]?.space.sort).toBe('name')
    expect(screen.getByRole('columnheader', { name: /Space 名稱/ })).toHaveAttribute('aria-sort', 'ascending')
  })

  it('名稱欄可以雙向排序：再點一次變降冪，表頭與偏好都跟著變', async () => {
    seed(forty())
    mount()
    const header = () => screen.getByRole('button', { name: /Space 名稱/ })
    await userEvent.click(header())
    await userEvent.click(header())
    expect(useListSortStore.getState().byViewer[VIEWER]?.space.sort).toBe('name-desc')
    expect(screen.getByRole('columnheader', { name: /Space 名稱/ })).toHaveAttribute('aria-sort', 'descending')
    // 🔴 降冪也是對全部資料排：升冪第一名在降冪裡是第 40 名，第一頁（25 列）不該有它；
    // 只反轉當頁的實作會把它留在第一頁最後一列
    expect(names()).toHaveLength(25)
    expect(names()).not.toContain('空間 01')
    await userEvent.click(header())
    expect(names()[0]).toBe('空間 01')
  })

  it('類型欄可以雙向排序', async () => {
    seed(forty())
    mount()
    const header = () => screen.getByRole('button', { name: /類型/ })
    await userEvent.click(header())
    expect(screen.getByRole('columnheader', { name: /類型/ })).toHaveAttribute('aria-sort', 'ascending')
    await userEvent.click(header())
    expect(useListSortStore.getState().byViewer[VIEWER]?.space.sort).toBe('type-desc')
    expect(screen.getByRole('columnheader', { name: /類型/ })).toHaveAttribute('aria-sort', 'descending')
  })

  it('換頁看得到第 26 列之後的資料', async () => {
    seed(forty())
    mount()
    await userEvent.click(screen.getByRole('button', { name: '第 2 頁' }))
    expect(names()).toHaveLength(15)
  })
})

describe('SpacesTable：點列、釘選', () => {
  it('點一列導到那個 Space 的 Summary 工作區', async () => {
    seed([space(1)])
    mount()
    await userEvent.click(screen.getByRole('cell', { name: '空間 01' }))
    expect(window.location.hash).toBe('#/summary/s01')
  })

  it('★ 釘選：呼叫既有的 togglePin，而且**不**觸發整列的導覽', async () => {
    seed([space(1)])
    mount()
    const star = screen.getByRole('button', { name: '釘選「空間 01」' })
    expect(star).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(star)
    expect(togglePin).toHaveBeenCalledWith(expect.objectContaining({ id: 'spaces/s01' }))
    expect(window.location.hash).toBe('#/summary')
  })

  it('釘選卡片列與 ★ 欄是同一份資料：pinned 的 Space 出現在卡片列，點卡片進工作區', async () => {
    seed([space(1), space(2, { pinned: true })])
    mount()
    const cards = screen.getByRole('region', { name: '已釘選的 Space' })
    expect(within(cards).getAllByRole('button')).toHaveLength(1)
    expect(screen.getByRole('button', { name: '釘選「空間 02」' })).toHaveAttribute('aria-pressed', 'true')

    await userEvent.click(within(cards).getByRole('button', { name: /空間 02/ }))
    expect(window.location.hash).toBe('#/summary/s02')
  })

  it('已釘選分頁只列 pinned', async () => {
    seed([space(1), space(2, { pinned: true })])
    mount()
    await userEvent.click(screen.getByRole('tab', { name: /已釘選/ }))
    expect(names()).toEqual(['空間 02'])
  })
})

describe('SpacesTable：Mention 欄只顯示拿得到的資料', () => {
  it('待處理 Mention 數依已載入的 Mention 算；「有新 Mention」分頁只留有待處理的', async () => {
    seed([space(1), space(2)], {
      items: [mention(1, 'spaces/s02'), mention(2, 'spaces/s02', 'manual'), mention(3, 'spaces/s01', 'resolved')],
      counts: { pending: 2, resolved: 1 },
    })
    mount()
    const row = screen.getByRole('cell', { name: '空間 02' }).closest('tr') as HTMLElement
    expect(within(row).getAllByRole('cell')[4]).toHaveTextContent('2')

    await userEvent.click(screen.getByRole('tab', { name: /有新 Mention/ }))
    expect(names()).toEqual(['空間 02'])
  })

  it('Mention 載入失敗時顯示「—」而不是 0，並說出原因', () => {
    seed([space(1)], { error: '後端沒有回應' })
    mount()
    const row = screen.getByRole('cell', { name: '空間 01' }).closest('tr') as HTMLElement
    expect(within(row).getAllByRole('cell')[4]).toHaveTextContent('—')
    expect(screen.getByText(/Mention 載入失敗/)).toHaveTextContent('後端沒有回應')
  })

  it('還沒載入（/me 說有 Mention，清單是空的）也是「—」；有新 Mention 分頁說不知道，不說沒有', async () => {
    seed([space(1)], { counts: { pending: 3, resolved: 0 } })
    mount()
    const row = screen.getByRole('cell', { name: '空間 01' }).closest('tr') as HTMLElement
    expect(within(row).getAllByRole('cell')[4]).toHaveTextContent('—')
    await userEvent.click(screen.getByRole('tab', { name: /有新 Mention/ }))
    expect(screen.getByText(/無法判斷/)).toBeInTheDocument()
  })

  it('🔴 收件匣被截斷、有待處理的沒載入時明示，不讓數字看起來是完整的', () => {
    seed([space(1)], {
      items: [mention(1, 'spaces/s01')],
      counts: { pending: 5, resolved: 0 },
    })
    mount()
    expect(screen.getByText(/另有 4 則較舊的待處理 Mention 沒有載入/)).toBeInTheDocument()
  })

  it('正對照：截斷的只有已處理時不顯示提示（每個 Space 的待處理數仍然完整）', () => {
    seed([space(1)], {
      items: [mention(1, 'spaces/s01')],
      counts: { pending: 1, resolved: 300 },
    })
    mount()
    expect(screen.queryByText(/較舊的待處理 Mention/)).toBeNull()
  })

  it('不出現已決定不做的「今日訊息」欄', () => {
    seed([space(1)])
    mount()
    const headers = screen.getAllByRole('columnheader').map((th) => th.textContent)
    expect(headers.join('|')).not.toMatch(/今日訊息/)
  })
})

describe('SpacesTable：Summary 欄', () => {
  function summaryCells(): string[] {
    const table = screen.getByRole('table', { name: 'Space 清單' })
    const headers = within(table).getAllByRole('columnheader').map((th) => th.textContent ?? '')
    const col = headers.findIndex((h) => h.includes('Summary'))
    return within(table)
      .getAllByRole('row')
      .slice(1)
      .map((row) => within(row).getAllByRole('cell')[col].textContent ?? '')
  }

  const fourKinds = () => [
    space(1, { displayName: '最新', lastSummaryAt: '2026-09-02T00:00:00+00:00' }),
    space(2, { displayName: '有新', lastActiveTime: '2026-09-03T00:00:00Z', lastSummaryAt: '2026-09-02T00:00:00+00:00' }),
    space(3, { displayName: '從未', lastSummaryAt: null }),
    space(4, { displayName: '不明', lastActiveTime: null, lastSummaryAt: '2026-09-02T00:00:00+00:00' }),
  ]

  it('依 lastActiveTime 與 lastSummaryAt 顯示三種狀態；活動時間缺少顯示「—」而不是「已是最新」', () => {
    seed(fourKinds())
    mount()
    const byName = Object.fromEntries(names().map((n, i) => [n, summaryCells()[i]]))
    expect(byName['最新']).toBe('已是最新')
    expect(byName['有新']).toBe('有新活動')
    expect(byName['從未']).toBe('從未產生')
    expect(byName['不明']).toMatch(/^—/)
    // 不顯示則數：格子裡不該出現數字
    expect(summaryCells().join('')).not.toMatch(/\d/)
  })

  it('點表頭依 Summary 狀態排序：有新活動 → 從未產生 → 已是最新，「—」排最後；再點一次反轉但「—」仍在最後', async () => {
    seed(fourKinds())
    mount()
    await userEvent.click(screen.getByRole('button', { name: /Summary/ }))
    expect(names()).toEqual(['有新', '從未', '最新', '不明'])
    expect(screen.getByRole('columnheader', { name: /Summary/ })).toHaveAttribute('aria-sort', 'ascending')
    await userEvent.click(screen.getByRole('button', { name: /Summary/ }))
    expect(names()).toEqual(['最新', '從未', '有新', '不明'])
    expect(screen.getByRole('columnheader', { name: /Summary/ })).toHaveAttribute('aria-sort', 'descending')
  })

  it('從 Summary 排序點回「最後活動」：回到最後活動新→舊，Summary 表頭不再標排序', async () => {
    seed(fourKinds())
    mount()
    await userEvent.click(screen.getByRole('button', { name: /Summary/ }))
    await userEvent.click(screen.getByRole('button', { name: /最後活動/ }))
    expect(names()[0]).toBe('有新')
    expect(useListSortStore.getState().byViewer[VIEWER]?.space.sort).toBe('activity-desc')
    expect(screen.getByRole('columnheader', { name: /Summary/ })).not.toHaveAttribute('aria-sort')
    expect(screen.getByRole('columnheader', { name: /最後活動/ })).toHaveAttribute('aria-sort', 'descending')
  })
})
