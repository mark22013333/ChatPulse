import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppShell } from './AppShell'
import { RouterProvider } from '@/router/useRouter'
import { useAuthStore } from '@/store/auth'
import { useDraftStore } from '@/store/draft'
import { resetListSortForTest } from '@/store/listSort'
import { useMentionsStore } from '@/store/mentions'
import { resetPageSizeForTest } from '@/store/pageSize'
import { useSpacesStore } from '@/store/spaces'
import { useSummaryStore } from '@/store/summary'
import { useUiStore } from '@/store/ui'
import type { Me, Space } from '@/lib/types'

/**
 * 設定頁在 AppShell 裡的位置（2026-10-01：從全螢幕對話框改成主體裡的一頁）。
 *
 * 守兩件事：
 *
 * 1. **側欄與頂列在設定時仍然在**——這是這次改動的目的（使用者回報「進設定後
 *    側欄不見了」）。所以這一份掛的是**真的** SideNav 與 TopBar，不是替身。
 * 2. 🔴 **開設定期間工作台不卸載**（設計規格 §7.2）。工作區換成計數用的替身，
 *    掛載幾次、卸載幾次一清二楚；串流旗標與 abort／reset 一起驗，
 *    「沒卸載但被中止了」也算壞。
 *
 * jsdom 沒有佈局，「看不看得見」驗的是收起來的手法（inert ＋ 透明、不是 display:none）。
 */

const summaryLife = vi.hoisted(() => ({ mounts: 0, unmounts: 0 }))
const draftLife = vi.hoisted(() => ({ mounts: 0, unmounts: 0 }))

vi.mock('@/app/useBootstrap', () => ({
  useBootstrap: () => ({ booting: false, authenticated: true }),
}))
vi.mock('@/app/StreamLiveRegions', () => ({ StreamLiveRegions: () => null }))
vi.mock('@/components/CommandPalette', () => ({ CommandPalette: () => null }))
vi.mock('@/components/ShortcutHelp', () => ({ ShortcutHelp: () => null }))
vi.mock('@/components/evidence/EvidenceDrawer', () => ({ EvidenceDrawer: () => null }))
vi.mock('@/components/evidence/EvidenceColumn', () => ({
  EvidenceColumn: () => null,
  // 頂列只讀 attentionCount
  useEvidenceBundle: () => ({ bundle: { attentionCount: 0 } }),
}))
vi.mock('@/components/SummaryHistory', () => ({ SummaryHistory: () => null }))
vi.mock('@/components/MentionInbox', () => ({ MentionInbox: () => <div>收件匣替身</div> }))
vi.mock('@/components/worklog/WorklogTable', () => ({ WorklogTable: () => null }))
vi.mock('@/components/worklog/WorklogDayWorkspace', () => ({ WorklogDayWorkspace: () => null }))
vi.mock('@/components/SummaryWorkspace', () => ({
  SummaryWorkspace: () => {
    useEffect(() => {
      summaryLife.mounts += 1
      return () => {
        summaryLife.unmounts += 1
      }
    }, [])
    return <div data-testid="summary-workspace" />
  },
}))
vi.mock('@/components/draft/DraftReplyWorkspace', () => ({
  DraftReplyWorkspace: () => {
    useEffect(() => {
      draftLife.mounts += 1
      return () => {
        draftLife.unmounts += 1
      }
    }, [])
    return <div data-testid="draft-workspace" />
  },
}))
// 設定的各分頁各自載入資料，與版面無關
vi.mock('@/components/settings/ReplyDefaultsPage', () => ({
  ReplyDefaultsPage: () => <div>回覆預設值內容</div>,
}))
vi.mock('@/components/settings/PersonasPage', () => ({ PersonasPage: () => <div>Persona 內容</div> }))

function space(n: number): Space {
  return {
    id: `spaces/s${String(n).padStart(2, '0')}`,
    displayName: `空間 ${String(n).padStart(2, '0')}`,
    type: 'SPACE',
    lastActiveTime: '2026-09-01T02:00:00Z',
  }
}

const summaryAbort = vi.fn()
const summaryReset = vi.fn()
const draftAbort = vi.fn()
const draftReset = vi.fn()

function mountAt(hash: string) {
  window.history.replaceState(null, '', hash)
  return render(
    <RouterProvider>
      <AppShell />
    </RouterProvider>,
  )
}

const sideNav = () => screen.getByRole('navigation', { name: '主導覽' })
const workbench = () => document.getElementById('workbench') as HTMLElement
const settingsPage = () => screen.queryByRole('region', { name: '設定' })

beforeEach(() => {
  try {
    localStorage.clear()
  } catch {
    // jsdom 一定有；保險起見
  }
  resetListSortForTest()
  resetPageSizeForTest()
  summaryLife.mounts = 0
  summaryLife.unmounts = 0
  draftLife.mounts = 0
  draftLife.unmounts = 0
  for (const fn of [summaryAbort, summaryReset, draftAbort, draftReset]) fn.mockClear()

  useUiStore.setState({ paletteOpen: false })
  useAuthStore.setState({
    me: { viewer: { id: 31, email: 'viewer@example.com', display_name: '林示範' } } as unknown as Me,
  })
  const items = Array.from({ length: 3 }, (_, i) => space(i + 1))
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
  useSummaryStore.setState({ streaming: false, abort: summaryAbort, reset: summaryReset } as never)
  useDraftStore.setState({ streaming: false, abort: draftAbort, reset: draftReset } as never)
})

afterEach(() => {
  useAuthStore.setState({ me: null })
})

describe('AppShell：設定是主體裡的一頁', () => {
  it('開設定時 SideNav 與頂列仍在：「設定」標 aria-current，頂列標題是「設定」', async () => {
    mountAt('#/summary')

    await userEvent.click(within(sideNav()).getByRole('link', { name: '設定' }))

    expect(window.location.hash).toBe('#/settings/reply')
    expect(settingsPage()).not.toBeNull()
    // 側欄沒有被蓋掉，也沒有被 inert
    expect(sideNav()).not.toHaveAttribute('inert')
    expect(sideNav().closest('[inert]')).toBeNull()
    expect(within(sideNav()).getByRole('link', { name: '設定' })).toHaveAttribute('aria-current', 'page')
    expect(sideNav().querySelectorAll('[aria-current="page"]')).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('設定')
    // 不再是 modal
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.querySelector('[aria-modal]')).toBeNull()
  })

  it('設定頁在主體那一格裡（與工作台同一個容器），不是 fixed 的全螢幕層', () => {
    mountAt('#/settings/reply')
    const page = settingsPage() as HTMLElement
    expect(page.parentElement?.parentElement).toBe(workbench().parentElement)
    expect(page.closest('.fixed')).toBeNull()
  })

  it('開設定時焦點移到設定內容區；SkipLink 也跳到那裡', () => {
    mountAt('#/settings/reply')
    expect(document.activeElement?.id).toBe('settings-main')
    expect(document.querySelector('a[href="#settings-main"]')).not.toBeNull()
  })

  it('工作台在設定時收起來：inert ＋ 透明，不是 display:none；關閉後恢復', async () => {
    mountAt('#/summary')
    // 正對照：沒開設定時工作台不是 inert
    expect(workbench()).not.toHaveAttribute('inert')

    act(() => {
      window.location.hash = '#/settings/reply'
      window.dispatchEvent(new HashChangeEvent('hashchange'))
    })

    expect(workbench()).toHaveAttribute('inert')
    expect(workbench().className).toMatch(/\bopacity-0\b/)
    expect(workbench().className).not.toMatch(/(^|\s)hidden(\s|$)/)
    expect(workbench().style.display).not.toBe('none')

    await userEvent.click(screen.getByRole('button', { name: '關閉' }))
    await waitFor(() => expect(window.location.hash).toBe('#/summary'))
    expect(settingsPage()).toBeNull()
    expect(workbench()).not.toHaveAttribute('inert')
  })

  it('🔴 Summary 串流中開設定、再關閉：工作區只掛載一次、從未卸載，串流沒被中止或清空', async () => {
    mountAt('#/summary/s01')
    useSummaryStore.setState({ streaming: true } as never)
    expect(summaryLife.mounts).toBe(1)
    const node = screen.getByTestId('summary-workspace')

    await userEvent.click(within(sideNav()).getByRole('link', { name: '設定' }))
    expect(settingsPage()).not.toBeNull()
    // 工作區還在 DOM 裡，而且是同一個節點
    expect(screen.getByTestId('summary-workspace')).toBe(node)
    expect(summaryLife.unmounts).toBe(0)

    await userEvent.click(screen.getByRole('link', { name: /^Persona/ }))
    await userEvent.click(screen.getByRole('button', { name: '關閉' }))
    await waitFor(() => expect(window.location.hash).toBe('#/summary/s01'))

    expect(screen.getByTestId('summary-workspace')).toBe(node)
    expect(summaryLife.mounts).toBe(1)
    expect(summaryLife.unmounts).toBe(0)
    expect(useSummaryStore.getState().streaming).toBe(true)
    expect(summaryAbort).not.toHaveBeenCalled()
    expect(summaryReset).not.toHaveBeenCalled()
  })

  it('🔴 Draft Reply 串流中開設定、從 SideNav 直接切回收件匣：工作區從未卸載', async () => {
    mountAt('#/mentions/7')
    useDraftStore.setState({ streaming: true } as never)
    expect(draftLife.mounts).toBe(1)

    await userEvent.click(within(sideNav()).getByRole('link', { name: '設定' }))
    expect(settingsPage()).not.toBeNull()
    expect(screen.getByTestId('draft-workspace')).toBeInTheDocument()

    // 在設定頁時點其他模組，直接切過去（不必先按關閉）
    await userEvent.click(within(sideNav()).getByRole('link', { name: /Mention 收件匣/ }))
    expect(window.location.hash).toMatch(/^#\/mentions/)
    expect(settingsPage()).toBeNull()
    expect(workbench()).not.toHaveAttribute('inert')

    expect(draftLife.mounts).toBe(1)
    expect(draftLife.unmounts).toBe(0)
    expect(useDraftStore.getState().streaming).toBe(true)
    expect(draftAbort).not.toHaveBeenCalled()
    expect(draftReset).not.toHaveBeenCalled()
  })

  it('正對照：計數替身確實數得到卸載', () => {
    const { unmount } = mountAt('#/summary/s01')
    expect(summaryLife.mounts).toBe(1)
    unmount()
    expect(summaryLife.unmounts).toBe(1)
  })
})
