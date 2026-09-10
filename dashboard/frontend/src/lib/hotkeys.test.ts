import { describe, expect, it } from 'vitest'
import { resolveHotkey, SEQUENCE_PREFIX } from '@/lib/hotkeys'

/**
 * 規格 §11.1 那張快捷鍵表的純函式層。
 *
 * `resolveHotkey` 原本的五個鍵（⌘K／⌘J／Esc／`/`／`?`）的測試在
 * `commands.test.ts`，那些**刻意留在原處不動**：它們用兩個參數呼叫
 * `resolveHotkey`，正好順便守著「新增 `pending` 參數沒有破壞既有呼叫端」。
 * 這一份只測這次補上的十個鍵。
 */

describe('兩鍵序列 g s／g m／g ,／g h', () => {
  it('按下 g 回報「等第二個鍵」，不是一個動作', () => {
    expect(resolveHotkey({ key: 'g' }, false)).toBe('sequence')
    expect(resolveHotkey({ key: 'G' }, false)).toBe('sequence')
  })

  it('四個目的地都對得上', () => {
    expect(resolveHotkey({ key: 's' }, false, SEQUENCE_PREFIX)).toBe('go-summary')
    expect(resolveHotkey({ key: 'm' }, false, SEQUENCE_PREFIX)).toBe('go-mentions')
    expect(resolveHotkey({ key: ',' }, false, SEQUENCE_PREFIX)).toBe('go-settings')
    expect(resolveHotkey({ key: 'h' }, false, SEQUENCE_PREFIX)).toBe('go-diagnostics')
  })

  it('連按兩次 g 是重新等第二個鍵，不是取消', () => {
    expect(resolveHotkey({ key: 'g' }, false, SEQUENCE_PREFIX)).toBe('sequence')
  })

  it('第二個鍵不在表上就什麼都不做（呼叫端據此清掉前綴）', () => {
    expect(resolveHotkey({ key: 'x' }, false, SEQUENCE_PREFIX)).toBeNull()
  })

  it('**序列的第二個鍵優先於同名的單鍵快捷鍵**', () => {
    // 沒有前綴時 `/` 是「跳到搜尋框」；`g` 之後按 `/` 不該還去跳搜尋框，
    // 不然使用者只會覺得序列時好時壞
    expect(resolveHotkey({ key: '/' }, false)).toBe('focus-search')
    expect(resolveHotkey({ key: '/' }, false, SEQUENCE_PREFIX)).toBeNull()
  })

  it('在輸入框裡打 g s 是打字，不是導覽', () => {
    expect(resolveHotkey({ key: 'g' }, true)).toBeNull()
    expect(resolveHotkey({ key: 's' }, true, SEQUENCE_PREFIX)).toBeNull()
  })
})

describe('帶 mod 的新組合鍵', () => {
  it('⌘Enter 是開始生成，Ctrl 版本一樣', () => {
    expect(resolveHotkey({ key: 'Enter', metaKey: true }, false)).toBe('generate')
    expect(resolveHotkey({ key: 'Enter', ctrlKey: true }, false)).toBe('generate')
  })

  it('⌘. 是停止串流', () => {
    expect(resolveHotkey({ key: '.', metaKey: true }, false)).toBe('stop')
  })

  it('⌘⇧C 是複製 Markdown', () => {
    // 實際事件在按住 Shift 時 key 會是大寫 C
    expect(resolveHotkey({ key: 'C', metaKey: true, shiftKey: true }, false)).toBe('copy')
  })

  it('**裸 ⌘C 一定要放過**（正對照：證明 Shift 那個守衛真的有咬）', () => {
    // 攔掉裸 ⌘C 的後果是：使用者選了一段文字按複製，剪貼簿裡卻是別的東西。
    // 那是靜默的資料錯誤，比快捷鍵不能用嚴重得多
    expect(resolveHotkey({ key: 'c', metaKey: true }, false)).toBeNull()
    expect(resolveHotkey({ key: 'c', ctrlKey: true }, false)).toBeNull()
  })

  it('mod 組合鍵在輸入框裡照樣生效', () => {
    expect(resolveHotkey({ key: 'Enter', metaKey: true }, true)).toBe('generate')
    expect(resolveHotkey({ key: '.', metaKey: true }, true)).toBe('stop')
  })
})

describe('[ 與 ] 換 Mention', () => {
  it('對應上一則與下一則', () => {
    expect(resolveHotkey({ key: '[' }, false)).toBe('prev-mention')
    expect(resolveHotkey({ key: ']' }, false)).toBe('next-mention')
  })

  it('在輸入框裡是括號，不是換 Mention', () => {
    expect(resolveHotkey({ key: '[' }, true)).toBeNull()
    expect(resolveHotkey({ key: ']' }, true)).toBeNull()
  })
})
