/**
 * 全域快捷鍵的判斷邏輯（設計規格 §11）。純函式，node 可測。
 */

import { READY_MODULES, type Section } from '@/lib/modules'

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
  /** 按下了兩鍵序列的前綴，還在等第二個鍵 */
  | 'sequence'
  /**
   * 導覽動作由模組表推導（`go-summary` | `go-mentions` | `go-settings`）。
   * 新增一個已上線的工作台時，這個聯集會自動長出對應的 action——
   * 呼叫端若有窮舉 switch，TypeScript 會直接在那裡報出「你還沒接上」。
   */
  | `go-${Section}`
  | 'go-diagnostics'
  | 'generate'
  | 'stop'
  | 'copy'
  | 'prev-mention'
  | 'next-mention'
  | null

/** 兩鍵序列的前綴。規格 §11.1 只有 `g` 一個（go）。 */
export const SEQUENCE_PREFIX = 'g'

/**
 * 兩鍵序列的逾時（規格 §11.1 寫的 1 秒）。
 *
 * **為什麼一定要有逾時**：`g` 自己不是快捷鍵，按下去畫面沒有任何變化。
 * 沒有逾時的話，一個誤觸的 `g` 會讓十分鐘後按的 `s` 突然跳頁，而使用者
 * 不可能把這兩件事聯想在一起——那是最難回報的一種 bug。
 */
export const SEQUENCE_TIMEOUT_MS = 1000

/**
 * 按下前綴之後，第二個鍵對應的動作。
 *
 * 模組的鍵位寫在模組表的 `hotkey` 欄位，這裡只負責組裝——同一件事不要在
 * 兩個檔案各記一份。診斷頁不是模組（它是設定底下的分頁），所以單獨列。
 */
const SEQUENCE_MAP: Record<string, HotkeyAction> = { h: 'go-diagnostics' }
for (const module of READY_MODULES) {
  if (module.hotkey) SEQUENCE_MAP[module.hotkey] = `go-${module.id}`
}

/**
 * 把一次按鍵解析成動作。
 *
 * 四條規則，順序有意義：
 * 1. **輸入法組字中一律不理**（由呼叫端先擋，這裡只處理其餘情況）
 * 2. 帶 mod 的組合鍵在輸入框裡也生效（⌘K、⌘Enter、Esc…）
 * 3. `pending` 有值時，這一鍵是兩鍵序列的第二個鍵——它優先於所有單鍵，
 *    不然 `g` 之後按 `/` 會變成「跳到搜尋框」，使用者只會覺得序列壞了
 * 4. 單鍵快捷鍵在輸入框裡**不**生效——不然打字就變成在下指令
 *
 * **送出回話與推播刻意不在這裡**：不可撤回的動作不該有肌肉記憶（§11.1）。
 * ⌘Enter 與 ⌘. 只碰「開始／停止生成」，兩者都可重來。
 *
 * @param pending 上一鍵留下的序列前綴（`SEQUENCE_PREFIX` 或 null）
 */
export function resolveHotkey(
  event: HotkeyEventLike,
  typing: boolean,
  pending: string | null = null,
): HotkeyAction {
  if (hasMod(event)) {
    const key = event.key.toLowerCase()
    if (key === 'k') return 'palette'
    if (key === 'j') return 'toggle-evidence'
    if (key === 'enter') return 'generate'
    if (key === '.') return 'stop'
    // **一定要有 Shift**：裸 ⌘C 是瀏覽器的複製，攔掉它會讓使用者選了一段
    // 文字卻複製到別的東西——那是靜默的資料錯誤，比快捷鍵不能用嚴重得多
    if (key === 'c' && event.shiftKey === true) return 'copy'
    return null
  }

  if (event.key === 'Escape') return 'escape'
  if (typing) return null

  if (pending === SEQUENCE_PREFIX) {
    const key = event.key.toLowerCase()
    // 連按兩次 `g` 視為重新開始等第二鍵，不要讓它變成「序列取消」
    if (key === SEQUENCE_PREFIX) return 'sequence'
    return SEQUENCE_MAP[key] ?? null
  }

  if (event.key === '/') return 'focus-search'
  if (event.key === '?') return 'help'
  if (event.key === '[') return 'prev-mention'
  if (event.key === ']') return 'next-mention'
  if (event.key.toLowerCase() === SEQUENCE_PREFIX) return 'sequence'
  return null
}

// 說明面板（`?`）的那張表在 `lib/shortcutHelp.ts`——它引用這個檔的
// SEQUENCE_PREFIX 與 HotkeyAction，並由 `shortcutHelp.test.ts` 逐條證明
// 表上的每一個鍵在 resolveHotkey 裡真的存在。改了上面的按鍵對應，
// 記得回頭看那張表。
