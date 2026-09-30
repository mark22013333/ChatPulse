import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'

/**
 * 三態輪替（深色 → 淺色 → 跟隨系統 → 深色）。
 *
 * mock 掉 next-themes 而不包真的 ThemeProvider：後者掛載時會呼叫
 * `window.matchMedia`，jsdom 沒有它。這裡要驗的是**我們**的輪替順序與
 * 「按了之後要求 next-themes 設成什麼」，不是 next-themes 本身。
 */
const state = vi.hoisted(() => ({
  theme: undefined as string | undefined,
  resolvedTheme: undefined as string | undefined,
  setTheme: vi.fn(),
}))
vi.mock('next-themes', () => ({ useTheme: () => state }))

import { ThemeToggle } from '@/components/ThemeToggle'

function clickAndGetRequested(): unknown {
  fireEvent.click(screen.getByRole('button'))
  return state.setTheme.mock.calls.at(-1)?.[0]
}

describe('ThemeToggle 三態輪替', () => {
  beforeEach(() => {
    state.setTheme.mockReset()
  })

  it.each([
    ['dark', 'dark', 'light'],
    ['light', 'light', 'system'],
    ['system', 'dark', 'dark'],
    ['system', 'light', 'dark'],
  ])('目前 %s（實際 %s）→ 按下後要求 %s', (theme, resolved, expected) => {
    state.theme = theme
    state.resolvedTheme = resolved
    render(<ThemeToggle />)
    expect(clickAndGetRequested()).toBe(expected)
  })

  it('跟隨系統時，可及名稱帶出實際生效的深淺', () => {
    state.theme = 'system'
    state.resolvedTheme = 'light'
    render(<ThemeToggle />)
    const name = screen.getByRole('button').getAttribute('aria-label') ?? ''
    expect(name).toContain('跟隨系統')
    expect(name).toContain('淺色')
  })

  it('讀不到 theme（沒有 Provider）時不猜成跟隨系統，退回用 resolvedTheme 判斷', () => {
    state.theme = undefined
    state.resolvedTheme = undefined
    render(<ThemeToggle />)
    const name = screen.getByRole('button').getAttribute('aria-label') ?? ''
    expect(name.startsWith('目前跟隨系統')).toBe(false)
    expect(clickAndGetRequested()).toBe('system')
  })
})
