import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { SideNav } from './SideNav'
import { RouterProvider } from '@/router/useRouter'
import { useMentionsStore } from '@/store/mentions'

/**
 * 左側主導覽（2026-10-01 改版，規格 `2026-10-01-table-layout.md` §3；
 * 導覽語意沿用 2026-09-09 §10.2）。
 *
 * 不驗顏色與寬度（jsdom 不算樣式）；驗的是改版時最容易被「照草稿抄」
 * 弄壞的語意：草稿用的是 `<button>`，這裡必須仍是 `<a href>` ＋ `aria-current`。
 */

function mountAt(hash: string) {
  window.history.replaceState(null, '', hash)
  return render(
    <RouterProvider>
      <SideNav />
    </RouterProvider>,
  )
}

beforeEach(() => {
  useMentionsStore.setState({ counts: { pending: 0, resolved: 0 } })
})

describe('SideNav', () => {
  it('是 <nav> 地標，模組是 <a href> 連結', () => {
    mountAt('#/summary')
    const nav = screen.getByRole('navigation', { name: '主導覽' })
    const link = within(nav).getByRole('link', { name: /工時草稿/ })
    expect(link).toHaveAttribute('href', '#/worklog')
  })

  it('**當前模組標 aria-current="page"，而且只有一個**', () => {
    mountAt('#/mentions')
    const nav = screen.getByRole('navigation', { name: '主導覽' })
    expect(within(nav).getByRole('link', { name: /Mention 收件匣/ })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(nav.querySelectorAll('[aria-current="page"]')).toHaveLength(1)
  })

  it('在設定頁時「設定」標 aria-current="page"，而且只有一個', () => {
    mountAt('#/settings/personas')
    const nav = screen.getByRole('navigation', { name: '主導覽' })
    expect(within(nav).getByRole('link', { name: /設定/ })).toHaveAttribute('aria-current', 'page')
    expect(nav.querySelectorAll('[aria-current="page"]')).toHaveLength(1)
  })

  it('**不補 tablist 語意**（§10.2：它本來就是連結）', () => {
    mountAt('#/summary')
    expect(screen.queryByRole('tablist')).toBeNull()
    expect(screen.queryByRole('tab')).toBeNull()
  })

  it('點連結會導到該模組', async () => {
    mountAt('#/summary')
    await userEvent.click(screen.getByRole('link', { name: /工時草稿/ }))
    expect(window.location.hash).toBe('#/worklog')
  })

  it('**固定寬度、不做收合**：沒有收合／展開鈕（規格 §3）', () => {
    mountAt('#/summary')
    expect(screen.queryByRole('button', { name: /收合|展開/ })).toBeNull()
  })

  it('主題切換已搬到頂列，導覽裡不再有', () => {
    mountAt('#/summary')
    const nav = screen.getByRole('navigation', { name: '主導覽' })
    expect(within(nav).queryByRole('button', { name: /切換為/ })).toBeNull()
    // 登出仍在導覽底部
    expect(within(nav).getByRole('button', { name: '登出' })).toBeInTheDocument()
  })

  it('尚未上線的模組不是連結，說明寫在畫面上', () => {
    mountAt('#/summary')
    const nav = screen.getByRole('navigation', { name: '主導覽' })
    expect(within(nav).queryByRole('link', { name: /統計/ })).toBeNull()
    expect(nav).toHaveTextContent('即將推出')
  })

  it('Mention 未處理數量顯示在收件匣項目上，0 就不顯示', () => {
    useMentionsStore.setState({ counts: { pending: 4, resolved: 0 } })
    const { unmount } = mountAt('#/summary')
    expect(screen.getByRole('link', { name: /Mention 收件匣/ })).toHaveTextContent('4')
    unmount()

    useMentionsStore.setState({ counts: { pending: 0, resolved: 0 } })
    mountAt('#/summary')
    expect(screen.getByRole('link', { name: /Mention 收件匣/ }).textContent).not.toMatch(/\d/)
  })

  it('**掛著 nav-surface**：index.css 靠它把 focus 環換成在深藍上看得見的顏色', () => {
    // 少了這個 class，淺色主題下導覽裡的 focus 環只剩 2.06:1（見 index.css）
    mountAt('#/summary')
    expect(screen.getByRole('navigation', { name: '主導覽' })).toHaveClass('nav-surface')
  })

  it('**渲染結果裡 [title] 選得到 0 個**（§17 紅線 8）', () => {
    const { container } = mountAt('#/summary')
    expect(container.querySelectorAll('[title]')).toHaveLength(0)
  })
})
