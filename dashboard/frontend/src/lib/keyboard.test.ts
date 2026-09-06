import { describe, expect, it, vi } from 'vitest'
import type { KeyboardEvent } from 'react'
import { isComposing, onEnter } from './keyboard'

/** 造一個夠像 React KeyboardEvent 的物件，只填我們會讀到的欄位。 */
function keyEvent(
  key: string,
  opts: { composing?: boolean; keyCode?: number } = {},
): KeyboardEvent {
  return {
    key,
    keyCode: opts.keyCode ?? 13,
    nativeEvent: { isComposing: opts.composing ?? false } as globalThis.KeyboardEvent,
    preventDefault: vi.fn(),
  } as unknown as KeyboardEvent
}

describe('isComposing', () => {
  it('一般按鍵不算組字中', () => {
    expect(isComposing(keyEvent('Enter'))).toBe(false)
  })

  it('nativeEvent.isComposing 為 true 時算組字中', () => {
    expect(isComposing(keyEvent('Enter', { composing: true }))).toBe(true)
  })

  it('keyCode 229 也算組字中（舊瀏覽器與部分 Safari 不設 isComposing）', () => {
    expect(isComposing(keyEvent('Enter', { keyCode: 229 }))).toBe(true)
  })
})

describe('onEnter', () => {
  it('按 Enter 會觸發', () => {
    const fn = vi.fn()
    onEnter(fn)(keyEvent('Enter'))
    expect(fn).toHaveBeenCalledOnce()
  })

  it('按其他鍵不觸發', () => {
    const fn = vi.fn()
    onEnter(fn)(keyEvent('a'))
    expect(fn).not.toHaveBeenCalled()
  })

  it('**輸入法組字中按 Enter 不觸發**（注音選字用的就是 Enter）', () => {
    const fn = vi.fn()
    onEnter(fn)(keyEvent('Enter', { composing: true }))
    expect(fn).not.toHaveBeenCalled()
  })

  it('keyCode 229 的 Enter 同樣不觸發', () => {
    const fn = vi.fn()
    onEnter(fn)(keyEvent('Enter', { keyCode: 229 }))
    expect(fn).not.toHaveBeenCalled()
  })

  it('選完字之後（組字結束）再按 Enter 才會送出', () => {
    const fn = vi.fn()
    const handler = onEnter(fn)
    handler(keyEvent('Enter', { composing: true })) // 選字
    expect(fn).not.toHaveBeenCalled()
    handler(keyEvent('Enter')) // 真的要送出了
    expect(fn).toHaveBeenCalledOnce()
  })
})
