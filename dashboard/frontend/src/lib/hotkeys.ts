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
  /** 按下了兩鍵序列的前綴，還在等第二個鍵 */
  | 'sequence'
  | 'go-summary'
  | 'go-mentions'
  | 'go-settings'
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

/** 按下前綴之後，第二個鍵對應的動作。 */
const SEQUENCE_MAP: Record<string, HotkeyAction> = {
  s: 'go-summary',
  m: 'go-mentions',
  ',': 'go-settings',
  h: 'go-diagnostics',
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

/**
 * 快捷鍵說明面板（`?`）的內容，同時是規格 §11.1 那張表的程式碼版本。
 *
 * **它為什麼放在這個檔而不是元件裡**：說明表與解析器一起漂移是這一類功能
 * 最典型的壞法——說明上寫著某個鍵，實際上沒有實作，而使用者按了沒反應
 * 只會以為自己記錯。放在同一個檔，`hotkeys.test.ts` 才能拿 `probe` 逐條
 * 重建事件、證明「說明表上的每一個全域鍵都真的解析得出動作」。
 *
 * 這件事在這個專案已經發生過一次：`lib/streamAnnouncements.ts` 原本要在完成
 * 宣告裡告知快捷鍵，卻因為那些鍵還沒實作而只能改講 landmark。
 */
export interface ShortcutHelpRow {
  /** 逐個畫成 `<kbd>` 的按鍵標籤 */
  keys: string[]
  label: string
  /**
   * `global`＝`resolveHotkey` 認得，測試會逐條驗證；
   * `list`＝由清單元件自己的 `onKeyDown` 處理，全域解析器看不到它。
   */
  scope: 'global' | 'list'
  /** scope 為 `global` 時的重建事件用資料。 */
  probe?: { key: string; mod?: boolean; shift?: boolean; pending?: string }
  /** scope 為 `global` 時，`probe` 應該解析出來的動作。 */
  action?: HotkeyAction
}

export const SHORTCUT_HELP: ShortcutHelpRow[] = [
  {
    keys: ['⌘', 'K'],
    label: '命令面板：搜尋 Space、Mention 與設定',
    scope: 'global',
    probe: { key: 'k', mod: true },
    action: 'palette',
  },
  {
    keys: ['Esc'],
    label: '由內而外關閉：對話框 → 命令面板 → 抽屜',
    scope: 'global',
    probe: { key: 'Escape' },
    action: 'escape',
  },
  {
    keys: ['G', 'S'],
    label: '前往摘要工作台',
    scope: 'global',
    probe: { key: 's', pending: SEQUENCE_PREFIX },
    action: 'go-summary',
  },
  {
    keys: ['G', 'M'],
    label: '前往 Mention 收件匣',
    scope: 'global',
    probe: { key: 'm', pending: SEQUENCE_PREFIX },
    action: 'go-mentions',
  },
  {
    keys: ['G', ','],
    label: '前往設定',
    scope: 'global',
    probe: { key: ',', pending: SEQUENCE_PREFIX },
    action: 'go-settings',
  },
  {
    keys: ['G', 'H'],
    label: '前往診斷',
    scope: 'global',
    probe: { key: 'h', pending: SEQUENCE_PREFIX },
    action: 'go-diagnostics',
  },
  {
    keys: ['/'],
    label: '焦點跳到左欄搜尋框',
    scope: 'global',
    probe: { key: '/' },
    action: 'focus-search',
  },
  {
    keys: ['⌘', 'J'],
    label: '開關證據欄',
    scope: 'global',
    probe: { key: 'j', mod: true },
    action: 'toggle-evidence',
  },
  { keys: ['↑', '↓'], label: '在左欄清單上下移動', scope: 'list' },
  { keys: ['Enter'], label: '開啟左欄清單上高亮的那一項', scope: 'list' },
  {
    keys: ['⌘', 'Enter'],
    label: '開始摘要／產生 Draft Reply',
    scope: 'global',
    probe: { key: 'Enter', mod: true },
    action: 'generate',
  },
  {
    keys: ['⌘', '.'],
    label: '停止串流',
    scope: 'global',
    probe: { key: '.', mod: true },
    action: 'stop',
  },
  {
    keys: ['⌘', '⇧', 'C'],
    label: '複製 Markdown',
    scope: 'global',
    probe: { key: 'C', mod: true, shift: true },
    action: 'copy',
  },
  {
    keys: ['['],
    label: '上一則 Mention',
    scope: 'global',
    probe: { key: '[' },
    action: 'prev-mention',
  },
  {
    keys: [']'],
    label: '下一則 Mention',
    scope: 'global',
    probe: { key: ']' },
    action: 'next-mention',
  },
  {
    keys: ['?'],
    label: '這張表',
    scope: 'global',
    probe: { key: '?' },
    action: 'help',
  },
]
