/**
 * 全域快捷鍵的判斷邏輯（設計規格 §11）。純函式，node 可測。
 */

/**
 * 這個事件的目標是不是輸入中的欄位？
 *
 * 刻意用鴨子型別而不是 `instanceof HTMLElement`：一來這個檔要能在 node
 * 環境（vitest）直接測，二來跨 iframe／realm 時 instanceof 本來就不可靠。
 */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!target || typeof target !== 'object') return false
  const el = target as {
    tagName?: string
    isContentEditable?: boolean
    getAttribute?: (name: string) => string | null
  }
  if (typeof el.tagName !== 'string') return false

  return (
    el.tagName === 'INPUT' ||
    el.tagName === 'TEXTAREA' ||
    el.tagName === 'SELECT' ||
    el.isContentEditable === true ||
    // base-ui 的 Select 觸發器等元件會自己接管鍵盤
    el.getAttribute?.('role') === 'combobox'
  )
}

export interface HotkeyEventLike {
  key: string
  metaKey?: boolean
  ctrlKey?: boolean
  shiftKey?: boolean
  altKey?: boolean
}

/** ⌘（macOS）或 Ctrl（其他）。兩個都接受，不必偵測平台。 */
export function hasMod(event: HotkeyEventLike): boolean {
  return event.metaKey === true || event.ctrlKey === true
}

export type HotkeyAction =
  | 'palette'
  | 'escape'
  | 'focus-search'
  | 'toggle-evidence'
  | 'help'
  | null

/**
 * 把一次按鍵解析成動作。
 *
 * 三條規則，順序有意義：
 * 1. **輸入法組字中一律不理**（由呼叫端先擋，這裡只處理其餘情況）
 * 2. 帶 mod 的組合鍵在輸入框裡也生效（⌘K、Esc）
 * 3. 單鍵快捷鍵在輸入框裡**不**生效——不然打字就變成在下指令
 *
 * **送出類動作刻意不在這裡**：不可撤回的動作不該有肌肉記憶。
 */
export function resolveHotkey(event: HotkeyEventLike, typing: boolean): HotkeyAction {
  if (hasMod(event)) {
    const key = event.key.toLowerCase()
    if (key === 'k') return 'palette'
    if (key === 'j') return 'toggle-evidence'
    return null
  }

  if (event.key === 'Escape') return 'escape'
  if (typing) return null

  if (event.key === '/') return 'focus-search'
  if (event.key === '?') return 'help'
  return null
}
