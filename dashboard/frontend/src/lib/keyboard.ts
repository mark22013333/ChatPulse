import type { KeyboardEvent } from 'react'

/**
 * 這次按鍵是不是「輸入法還在組字」的狀態。
 *
 * **為什麼一定要檢查**：中文輸入法在選字時，Enter 的意思是「就選這個字」，
 * 不是「送出表單」。如果 keydown handler 直接看 `e.key === 'Enter'` 就送出，
 * 使用者打「李」按 Enter 選字的瞬間，表單就被送掉了——他連字都還沒選完。
 *
 * 這個專案的使用者是台灣同事，注音是預設輸入法，所以這不是邊角情況，
 * 是**每次輸入中文都會遇到**。2026-09-06 由使用者實機回報：
 * 在「為這個空間取個名字」輸入「李」按 Enter 選字，直接就儲存了。
 *
 * 兩個判斷都要：
 * - `nativeEvent.isComposing` 是標準做法
 * - `keyCode === 229` 是 IME 的通用鍵碼，補上舊瀏覽器與 Safari 某些版本
 *   不設 isComposing 的情況
 */
export function isComposing(event: KeyboardEvent): boolean {
  return event.nativeEvent.isComposing || event.keyCode === 229
}

/**
 * 「按 Enter 送出」的標準寫法，已經擋掉輸入法組字。
 *
 * 用法：`onKeyDown={onEnter(() => void submit())}`
 */
export function onEnter(handler: () => void) {
  return (event: KeyboardEvent) => {
    if (isComposing(event)) return
    if (event.key !== 'Enter') return
    event.preventDefault()
    handler()
  }
}
