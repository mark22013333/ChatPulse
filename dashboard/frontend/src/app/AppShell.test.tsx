import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppShell } from './AppShell'
import { RouterProvider } from '@/router/useRouter'
import { useAuthStore } from '@/store/auth'
import { resetListSortForTest } from '@/store/listSort'
import { useMentionsStore } from '@/store/mentions'
import { resetPageSizeForTest } from '@/store/pageSize'
import { useSpacesStore } from '@/store/spaces'
import { useSummaryStore } from '@/store/summary'
import type { Me, Space } from '@/lib/types'

/**
 * AppShell 的摘要工作台：表格 ↔ Summary 工作區的整頁切換（2026-10-01 改版 §4.1）。
 *
 * 這一份守兩件「錯了畫面看不出來」的事：
 *
 * 1. **表格收起來時沒有被卸載。** tabs、篩選、頁碼都是 SpacesTable 的元件
 *    state，卸載一次就全部歸零。所以不只驗「返回後狀態還在」，還直接驗
 *    「是同一個 DOM 節點」——狀態還在也可能是剛好預設值相同。
 * 2. **Summary 工作區在切換中沒有被卸載**（§7.2 保留掛載的原意：串流不中斷）。
 *    工作區換成計數用的替身：掛載幾次、卸載幾次一清二楚。
 *
 * **這裡不驗「哪一半看得見」**：jsdom 沒有佈局，驗的是收起來的手法本身
 * （inert ＋ absolute ＋ 透明、不是 display:none）。其餘模組換成替身，
 * 這一份只關心 AppShell 的骨架。
 */

const lifecycle = vi.hoisted(() => ({ mounts: 0, unmounts: 0, lastActive: null as boolean | null }))

vi.mock('@/app/useBootstrap', () => ({
  useBootstrap: () => ({ booting: false, authenticated: true }),
}))
vi.mock('@/app/SideNav', () => ({ SideNav: () => null }))
vi.mock('@/app/TopBar', () => ({ TopBar: () => null }))
vi.mock('@/app/StreamLiveRegions', () => ({ StreamLiveRegions: () => null }))
vi.mock('@/components/CommandPalette', () => ({ CommandPalette: () => null }))
vi.mock('@/components/ShortcutHelp', () => ({ ShortcutHelp: () => null }))
vi.mock('@/components/evidence/EvidenceDrawer', () => ({ EvidenceDrawer: () => null }))
vi.mock('@/components/evidence/EvidenceColumn', () => ({
  EvidenceColumn: ({ origin }: { origin: string }) => <div data-testid={`evidence-${origin}`} />,
}))
vi.mock('@/components/SummaryHistory', () => ({ SummaryHistory: () => null }))
vi.mock('@/components/MentionInbox', () => ({ MentionInbox: () => <div>收件匣替身</div> }))
vi.mock('@/components/draft/DraftReplyWorkspace', () => ({ DraftReplyWorkspace: () => null }))
vi.mock('@/components/worklog/WorklogWeekRail', () => ({ WorklogWeekRail: () => null }))
vi.mock('@/components/worklog/WorklogDayWorkspace', () => ({ WorklogDayWorkspace: () => null }))
vi.mock('@/components/SummaryWorkspace', () => ({
  SummaryWorkspace: ({ space, active }: { space: Space | null; active?: boolean }) => {
    lifecycle.lastActive = active ?? null
    useEffect(() => {
      lifecycle.mounts += 1
      return () => {
        lifecycle.unmounts += 1
      }
    }, [])
    return <div data-testid="summary-workspace">{space?.displayName ?? '（未選）'}</div>
  },
}))

function space(n: number): Space {
  return {
    id: `spaces/s${String(n).padStart(2, '0')}`,
    displayName: `空間 ${String(n).padStart(2, '0')}`,
    type: 'SPACE',
    lastActiveTime: '2026-09-01T02:00:00Z',
  }
}

const abort = vi.fn()
const reset = vi.fn()

function mountAt(hash: string) {
  window.history.replaceState(null, '', hash)
  return render(
    <RouterProvider>
      <AppShell />
    </RouterProvider>,
  )
}

/** 模擬 Viewer 在網址列換位置（與返回鍵、點列走同一條 hashchange 路徑）。 */
function go(hash: string) {
  act(() => {
    window.location.hash = hash
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
}

const rail = () => document.getElementById('rail') as HTMLElement
const main = () => document.getElementById('main') as HTMLElement
const table = () => screen.getByRole('table', { name: 'Space 清單' })

/** 收起來的那一半：留著掛載、inert、移出版面流、透明——**不是 display:none**。 */
function expectTuckedAway(el: HTMLElement) {
  expect(el).toHaveAttribute('inert')
  expect(el.className).toMatch(/\babsolute\b/)
  expect(el.className).toMatch(/\bopacity-0\b/)
  expect(el.className).not.toMatch(/(^|\s)hidden(\s|$)/)
  expect(el.style.display).not.toBe('none')
}

beforeEach(() => {
  try {
    localStorage.clear()
  } catch {
    // jsdom 一定有；保險起見
  }
  resetListSortForTest()
  resetPageSizeForTest()
  useAuthStore.setState({ me: { viewer: { id: 31 } } as unknown as Me })
  lifecycle.mounts = 0
  lifecycle.unmounts = 0
  abort.mockClear()
  reset.mockClear()
  const items = Array.from({ length: 40 }, (_, i) => space(i + 1))
  useSpacesStore.setState({
    items,
    total: items.length,
    loading: false,
    error: null,
    selectedId: null,
    load: async () => {},
  })
  useMentionsStore.setState({
    items: [],
    counts: { pending: 0, resolved: 0 },
    loading: false,
    error: null,
    startPolling: () => {},
    stopPolling: () => {},
  })
  useSummaryStore.setState({ streaming: false, abort, reset } as never)
})

afterEach(() => {
  useAuthStore.setState({ me: null })
})

describe('AppShell：摘要工作台是整頁表格', () => {
  it('#/summary 顯示表格與分頁器，工作區收起來；沒有返回列', () => {
    mountAt('#/summary')
    expect(rail()).not.toHaveAttribute('inert')
    expect(within(rail()).getByRole('table', { name: 'Space 清單' })).toBeInTheDocument()
    expect(within(rail()).getByRole('navigation', { name: 'Space 清單分頁' })).toBeInTheDocument()
    expectTuckedAway(main())
    expect(screen.queryByRole('button', { name: '返回 Space 清單' })).toBeNull()
  })

  it('#/summary/:key 顯示工作區與返回列，表格收起來（沒有卸載）', () => {
    mountAt('#/summary/s03')
    expect(main()).not.toHaveAttribute('inert')
    expect(screen.getByTestId('summary-workspace')).toHaveTextContent('空間 03')
    expectTuckedAway(rail())
    // 表格還在 DOM 裡（收起來，不是拿掉）
    expect(rail().querySelector('table')).not.toBeNull()

    const back = screen.getByRole('button', { name: '返回 Space 清單' })
    // 表格型的返回列在所有寬度都出現——沒有 lg:hidden
    expect(back.closest('nav')?.className).not.toMatch(/lg:hidden/)
  })

  it('表格獨占主體時證據欄也收起來；進到工作區才出現', () => {
    mountAt('#/summary')
    const evidence = screen.getByRole('complementary', { name: '證據', hidden: true })
    // 不能套 expectTuckedAway：證據欄在 <1280 本來就是 `hidden xl:flex`（改版前的
    // 既有行為，那個寬度走抽屜）。這裡驗的是 ≥1280 時它也用 inert ＋ 透明收起來
    expect(evidence).toHaveAttribute('inert')
    expect(evidence.className).toMatch(/\bxl:flex\b/)
    expect(evidence.className).toMatch(/\babsolute\b/)
    expect(evidence.className).toMatch(/\bopacity-0\b/)
    go('#/summary/s01')
    expect(evidence).not.toHaveAttribute('inert')
  })

  it('🔴 點列進工作區、按返回：篩選、排序、頁碼都還在，而且是同一張表（沒有重掛）', async () => {
    mountAt('#/summary')
    const before = table()

    await userEvent.click(screen.getByRole('button', { name: /Space 名稱/ }))
    await userEvent.type(screen.getByRole('textbox', { name: '搜尋 Space 名稱' }), '空間')
    await userEvent.click(screen.getByRole('button', { name: '第 2 頁' }))
    expect(screen.getByRole('button', { name: '第 2 頁' })).toHaveAttribute('aria-current', 'page')

    // 第 2 頁第一列是「空間 26」（依名稱排、每頁 25）
    await userEvent.click(screen.getByRole('cell', { name: '空間 26' }))
    expect(window.location.hash).toBe('#/summary/s26')
    expectTuckedAway(rail())

    await userEvent.click(screen.getByRole('button', { name: '返回 Space 清單' }))
    expect(window.location.hash).toBe('#/summary')

    expect(table()).toBe(before)
    expect(screen.getByRole('textbox', { name: '搜尋 Space 名稱' })).toHaveValue('空間')
    expect(screen.getByRole('button', { name: '第 2 頁' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('columnheader', { name: /Space 名稱/ })).toHaveAttribute('aria-sort', 'ascending')
  })

  it('捲動位置：表格的捲動容器在切換前後是同一個節點（不是 display:none，尺寸不歸零）', () => {
    mountAt('#/summary')
    const scroller = table().closest('.overflow-y-auto') as HTMLElement
    expect(scroller).not.toBeNull()
    scroller.scrollTop = 480
    go('#/summary/s05')
    go('#/summary')
    expect(table().closest('.overflow-y-auto')).toBe(scroller)
    // jsdom 會記住 scrollTop 的指派；實際瀏覽器的捲動行為由 E2E 驗（規格 §15.4）
    expect(scroller.scrollTop).toBe(480)
  })

  it('🔴 串流中來回切換，Summary 工作區只掛載一次、從未卸載，store 沒被中止或清空', () => {
    mountAt('#/summary/s01')
    useSummaryStore.setState({ streaming: true } as never)
    expect(lifecycle.mounts).toBe(1)

    go('#/summary')
    go('#/summary/s01')
    go('#/summary')
    go('#/summary/s01')

    expect(lifecycle.mounts).toBe(1)
    expect(lifecycle.unmounts).toBe(0)
    // 切到表格時工作區仍是「作用中的工作台」，不會被當成別的模組而暫停
    expect(lifecycle.lastActive).toBe(true)
    expect(useSummaryStore.getState().streaming).toBe(true)
    expect(abort).not.toHaveBeenCalled()
    expect(reset).not.toHaveBeenCalled()
  })

  it('正對照：Mention 收件匣在寬螢幕仍是主從並排，清單不收、不獨占', () => {
    mountAt('#/mentions/7')
    expect(rail()).not.toHaveAttribute('inert')
    expect(main()).not.toHaveAttribute('inert')
    expect(rail().className).toMatch(/lg:w-inbox/)
    const back = screen.getByRole('button', { name: '返回 Mention 收件匣' })
    expect(back.closest('nav')?.className).toMatch(/lg:hidden/)
  })
})
