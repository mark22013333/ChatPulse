import { describe, expect, it } from 'vitest'
import { resolveHotkey, type HotkeyAction } from '@/lib/hotkeys'
import { SHORTCUT_HELP } from '@/lib/shortcutHelp'

/**
 * 說明表與實作的漂移守衛。
 *
 * 這是這一組測試裡最重要的一支：說明面板上寫著某個鍵、實際上沒有實作，
 * 使用者按了沒反應只會以為自己記錯——而畫面上看不出任何錯誤。這個專案已經
 * 因為「表上有、實作沒有」而讓 §10.6 的完成宣告一度只能改講 landmark
 * （見 `lib/streamAnnouncements.ts` 的註解），所以這件事要機械化守住。
 */
describe('SHORTCUT_HELP 與解析器不得漂移', () => {
  const global = SHORTCUT_HELP.filter((row) => row.scope === 'global')

  it('每一列全域快捷鍵都真的解析得出它宣稱的動作', () => {
    expect(global.length).toBeGreaterThan(0)
    for (const row of global) {
      expect(row.probe, `${row.keys.join('+')} 少了 probe`).toBeDefined()
      const probe = row.probe!
      const action = resolveHotkey(
        { key: probe.key, metaKey: probe.mod, shiftKey: probe.shift },
        false,
        probe.pending ?? null,
      )
      expect(action, `說明表寫 ${row.keys.join('+')} → ${row.action}，實際解析出 ${action}`).toBe(
        row.action,
      )
    }
  })

  it('**故意寫錯一列會被抓到**（正對照）', () => {
    // 上面那條回報「全部相符」時，必須先證明它抓得到不相符的情況
    const wrong = resolveHotkey({ key: 'q', metaKey: true }, false)
    expect(wrong).not.toBe('palette')
    expect(wrong).toBeNull()
  })

  it('§11.1 表上的每一個動作都有一列說明', () => {
    const covered = new Set(SHORTCUT_HELP.map((row) => row.action).filter(Boolean))
    // `sequence` 是序列的中間狀態，不是使用者看得到的動作，所以不列進說明
    const expected = ALL_ACTIONS.filter((action) => action !== 'sequence')
    for (const action of expected) {
      expect(covered.has(action), `${action} 沒有出現在說明表裡`).toBe(true)
    }
  })

  it('清單類的鍵標成 list，不假裝是全域的', () => {
    // ↑↓ 與 Enter 由 SpaceList／MentionInbox 自己的 onKeyDown 處理，
    // resolveHotkey 看不到它們。標成 global 會讓上面那條漂移守衛紅
    const list = SHORTCUT_HELP.filter((row) => row.scope === 'list')
    expect(list.map((row) => row.keys.join(' '))).toEqual(['↑ ↓', 'Enter'])
    expect(list.every((row) => row.action === undefined)).toBe(true)
  })
})

/**
 * `HotkeyAction` 的執行期清單。
 *
 * 下面那個 `never[]` 賦值是**編譯期**的窮盡檢查：往 `HotkeyAction` 加了新動作
 * 卻忘了加進這裡，`tsc --noEmit` 就會紅。少了它，上面「每個動作都有說明」
 * 那條會安靜地漏掉新動作——把關工具本身也要有守衛。
 */
const ALL_ACTIONS = [
  'palette',
  'escape',
  'focus-search',
  'toggle-evidence',
  'help',
  'sequence',
  'go-summary',
  'go-mentions',
  'go-settings',
  'go-diagnostics',
  'generate',
  'stop',
  'copy',
  'prev-mention',
  'next-mention',
] as const

type MissingFromList = Exclude<NonNullable<HotkeyAction>, (typeof ALL_ACTIONS)[number]>
const _exhaustive: never[] = [] as MissingFromList[]
void _exhaustive
