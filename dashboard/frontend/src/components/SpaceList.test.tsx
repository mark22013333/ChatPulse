import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SpaceList } from './SpaceList'
import type { Space } from '@/lib/types'

/**
 * jsdom 沒有佈局，真的虛擬清單量不到高度、一列都不會掛出來（規格 §15.4：
 * 不要在 jsdom 驗真實捲動位置）。這裡把 virtualizer 換成固定只掛前 13 列的
 * 假件，保留「**只有一部分列在 DOM 裡**」這個唯一與鍵盤導航相關的性質，
 * 順便讓 scrollToIndex 可被斷言。
 */
const { scrollToIndex, MOUNTED } = vi.hoisted(() => ({
  scrollToIndex: vi.fn(),
  MOUNTED: 13, // 可視範圍 ＋ overscan 12
}))

vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getTotalSize: () => count * 52,
    getVirtualItems: () =>
      Array.from({ length: Math.min(count, MOUNTED) }, (_, i) => ({
        index: i,
        key: i,
        start: i * 52,
        size: 52,
      })),
    measureElement: () => {},
    scrollToIndex,
  }),
}))

function space(n: number): Space {
  return {
    id: `spaces/s${n}`,
    displayName: `空間 ${n}`,
    type: 'SPACE',
    lastActiveTime: '2026-09-10T02:00:00Z',
    renamable: false,
    nameSource: null,
    pinned: false,
  }
}

const SPACES = Array.from({ length: 20 }, (_, i) => space(i))

function options() {
  return screen.getAllByRole('option')
}

function tabbable() {
  return options().filter((el) => el.getAttribute('tabindex') === '0')
}

function activeOptionIndex() {
  return document.activeElement?.getAttribute('data-option-index')
}

beforeEach(() => {
  scrollToIndex.mockClear()
})

describe('SpaceList 的鍵盤導航（roving tabindex）', () => {
  it('是有名字的 listbox，每一列是 option', () => {
    render(<SpaceList spaces={SPACES} label="要做摘要的 Space" onSelect={() => {}} />)

    expect(screen.getByRole('listbox', { name: '要做摘要的 Space' })).toBeInTheDocument()
    expect(options()).toHaveLength(MOUNTED)
  })

  it('**整份清單只占一個 Tab 停留點**——同時只有一個 tabIndex=0', async () => {
    render(<SpaceList spaces={SPACES} onSelect={() => {}} />)
    expect(tabbable()).toHaveLength(1)

    await userEvent.tab()
    await userEvent.keyboard('{ArrowDown}{ArrowDown}')

    // 走了兩列之後仍然只有一個。436 筆各自可 Tab 的話，鍵盤使用者要按
    // 436 次才走得完這一欄——那正是這條要擋住的回歸。
    expect(tabbable()).toHaveLength(1)
    expect(tabbable()[0]).toHaveAttribute('data-option-index', '2')
  })

  it('↑↓ 移動焦點', async () => {
    render(<SpaceList spaces={SPACES} onSelect={() => {}} />)

    await userEvent.tab()
    expect(activeOptionIndex()).toBe('0')

    await userEvent.keyboard('{ArrowDown}{ArrowDown}')
    expect(activeOptionIndex()).toBe('2')

    await userEvent.keyboard('{ArrowUp}')
    expect(activeOptionIndex()).toBe('1')
  })

  it('**移動時用 align: auto 捲動**，不是 center', async () => {
    render(<SpaceList spaces={SPACES} onSelect={() => {}} />)

    await userEvent.tab()
    await userEvent.keyboard('{ArrowDown}')

    // center 會讓每一次 ↓ 都把清單重新置中，看起來像整份清單在跳
    expect(scrollToIndex).toHaveBeenCalledWith(1, { align: 'auto' })
  })

  it('Home 回到第一列；到頂之後再按 ↑ 停在原地（不繞回底部）', async () => {
    render(<SpaceList spaces={SPACES} onSelect={() => {}} />)

    await userEvent.tab()
    await userEvent.keyboard('{ArrowDown}{ArrowDown}{ArrowDown}')
    expect(activeOptionIndex()).toBe('3')

    await userEvent.keyboard('{Home}')
    expect(activeOptionIndex()).toBe('0')

    await userEvent.keyboard('{ArrowUp}')
    expect(activeOptionIndex()).toBe('0')
  })

  it('**active 捲出可視範圍時，Tab 停留點讓給第一個還掛著的列**', async () => {
    // 不這樣做的話整份清單會沒有任何 tabIndex=0 的節點，Tab 進不來——
    // roving tabindex 配虛擬滾動特有的坑
    render(<SpaceList spaces={SPACES} onSelect={() => {}} />)

    await userEvent.tab()
    await userEvent.keyboard('{End}') // 第 19 列，超出掛載範圍（0–12）

    expect(scrollToIndex).toHaveBeenCalledWith(19, { align: 'auto' })
    expect(tabbable()).toHaveLength(1)
    expect(tabbable()[0]).toHaveAttribute('data-option-index', '0')
  })

  it('Enter 選取目前這一列', async () => {
    const onSelect = vi.fn()
    render(<SpaceList spaces={SPACES} onSelect={onSelect} />)

    await userEvent.tab()
    await userEvent.keyboard('{ArrowDown}{Enter}')

    expect(onSelect).toHaveBeenCalledWith(SPACES[1])
  })

  it('複選模式：Enter 是 toggle，選取狀態由 aria-selected 承擔', async () => {
    const onToggle = vi.fn()
    render(<SpaceList spaces={SPACES} checkedIds={[SPACES[0].id]} onToggle={onToggle} />)

    expect(screen.getByRole('listbox')).toHaveAttribute('aria-multiselectable', 'true')
    expect(options()[0]).toHaveAttribute('aria-selected', 'true')
    expect(options()[1]).toHaveAttribute('aria-selected', 'false')

    await userEvent.tab()
    await userEvent.keyboard('{Enter}')
    expect(onToggle).toHaveBeenCalledWith(SPACES[0])
  })

  it('**不是導航鍵就不吃掉**（正對照：Tab 走得出這份清單）', async () => {
    render(
      <>
        <SpaceList spaces={SPACES} onSelect={() => {}} />
        <button type="button">清單後面的按鈕</button>
      </>,
    )

    await userEvent.tab()
    expect(activeOptionIndex()).toBe('0')

    await userEvent.tab()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '清單後面的按鈕' }))
  })

  it('單選模式下 aria-selected 跟著 selectedId', () => {
    render(<SpaceList spaces={SPACES} selectedId={SPACES[2].id} onSelect={() => {}} />)

    expect(options()[2]).toHaveAttribute('aria-selected', 'true')
    expect(options()[0]).toHaveAttribute('aria-selected', 'false')
  })
})
