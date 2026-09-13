import { SEQUENCE_PREFIX, type HotkeyAction } from '@/lib/hotkeys'

/**
 * 快捷鍵說明面板（`?`）的內容，同時是規格 §11.1 那張表的程式碼版本。
 *
 * **它為什麼是資料而不是寫在元件的 JSX 裡**：說明表與解析器一起漂移是這一類
 * 功能最典型的壞法——說明上寫著某個鍵、實際上沒有實作，而使用者按了沒反應
 * 只會以為自己記錯。做成資料，`shortcutHelp.test.ts` 才能拿每一列的 `probe`
 * 重建事件，證明「說明表上的每一個全域鍵都真的解析得出它宣稱的動作」。
 *
 * 這件事在這個專案已經發生過一次：`lib/streamAnnouncements.ts` 原本要在完成
 * 宣告裡告知快捷鍵，卻因為那些鍵還沒實作而只能改講 landmark。
 *
 * **與 `hotkeys.ts` 分成兩個檔的理由**：規格 §9.2 給 `hotkeys.ts` 的定位是
 * 「快捷鍵比對與 isTypingTarget」約 120 行，說明表塞進去會讓它變成 259 行、
 * 破掉 §14 P4 的 250 行門檻。分檔不影響漂移守衛——守衛要的是「測試同時看得到
 * 兩邊」，不是「兩邊在同一個檔」。
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
