import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { MasterDetailBack } from './MasterDetailBack'
import { RouterProvider } from '@/router/useRouter'

/**
 * 主從切換的返回麵包屑（規格 §12）。
 *
 * **這裡不驗「哪一半看得見」。** jsdom 沒有佈局也不算 media query，
 * `max-lg:hidden` 在這裡與沒寫一樣——量不出來的東西不該在這一層假裝驗過。
 * 版面本身由瀏覽器 E2E 在 900px 寬度實測（`tests/e2e/test_ui_redesign.cjs`
 * 第 14 節，含 1300px 的正對照）。這一份只驗這顆按鈕本身：它存在、說得出
 * 要回哪裡、按下去導到沒有 id 的位置。
 */

function mountAt(hash: string, view: 'summary' | 'mentions') {
  window.history.replaceState(null, '', hash)
  return render(
    <RouterProvider>
      <MasterDetailBack view={view} />
    </RouterProvider>,
  )
}

beforeEach(() => {
  window.history.replaceState(null, '', '#/summary')
})

describe('MasterDetailBack', () => {
  it('摘要工作區回 Space 清單', async () => {
    mountAt('#/summary/AAQATjybbSY', 'summary')

    await userEvent.click(screen.getByRole('button', { name: '返回 Space 清單' }))
    expect(window.location.hash).toBe('#/summary')
  })

  it('草稿工作區回 Mention 收件匣', async () => {
    mountAt('#/mentions/65', 'mentions')

    await userEvent.click(screen.getByRole('button', { name: '返回 Mention 收件匣' }))
    expect(window.location.hash).toBe('#/mentions')
  })

  it('**是 nav 不是一顆孤立的按鈕**（麵包屑要說得出自己是什麼）', () => {
    mountAt('#/mentions/65', 'mentions')
    expect(screen.getByRole('navigation', { name: '麵包屑' })).toBeInTheDocument()
  })

  it('文字說得出目的地，不是只寫「返回」', () => {
    // 「返回」在兩個工作台上是兩個不同的地方，只寫「返回」等於沒說
    mountAt('#/summary/x', 'summary')
    expect(screen.queryByRole('button', { name: '返回' })).toBeNull()
  })

  it('**渲染結果裡 [title] 選得到 0 個**（規格 §10.3）', () => {
    const { container } = mountAt('#/mentions/65', 'mentions')
    expect(container.querySelectorAll('[title]')).toHaveLength(0)
  })
})
