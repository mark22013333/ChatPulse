import { describe, expect, it } from 'vitest'
import { buildCommands, filterCommands, type Command } from '@/lib/commands'
import { isTypingTarget, resolveHotkey } from '@/lib/hotkeys'
import type { Mention, Space } from '@/lib/types'

const spaceTypeLabel = () => '群組'
const relativeTime = () => '1 小時前'

function space(id: string, name: string, pinned = false): Space {
  return { id, displayName: name, type: 'SPACE', lastActiveTime: '', pinned } as Space
}

function mention(id: number, sender: string, spaceName: string, text = ''): Mention {
  return { id, sender_display: sender, space_name: spaceName, text, state: 'pending' } as Mention
}

function commands(): Command[] {
  return buildCommands({
    spaces: [
      space('spaces/A', '後端維運'),
      space('spaces/B', '前端維運', true),
      space('spaces/C', '行銷週會'),
    ],
    mentions: [mention(47, '王小明', '架構組', 'production 的 timeout 是多少')],
    spaceTypeLabel,
    relativeTime,
  })
}

describe('buildCommands', () => {
  it('Space 導向摘要工作台、Mention 導向收件匣', () => {
    const all = commands()
    expect(all.find((c) => c.id === 'space-spaces/A')?.href).toBe('#/summary/A')
    expect(all.find((c) => c.id === 'mention-47')?.href).toBe('#/mentions/47')
  })

  it('**送出類動作不得進命令面板**——不可撤回的動作不該有肌肉記憶', () => {
    const labels = commands().map((c) => c.label)
    for (const forbidden of ['送出', '推播', '刪除']) {
      expect(labels.some((l) => l.includes(forbidden))).toBe(false)
    }
  })
})

describe('filterCommands', () => {
  it('沒輸入時不倒一整面牆，只給導覽＋釘選＋幾則 Mention', () => {
    const result = filterCommands(commands(), '')
    expect(result.length).toBeLessThanOrEqual(12)
    expect(result.some((c) => c.group === '前往')).toBe(true)
    expect(result.some((c) => c.pinned)).toBe(true)
    // 沒釘選的一般 Space 不該在預設清單裡
    expect(result.some((c) => c.label === '行銷週會')).toBe(false)
  })

  it('關鍵字比對 Space 名稱', () => {
    const result = filterCommands(commands(), '維運')
    expect(result.map((c) => c.label)).toContain('後端維運')
    expect(result.map((c) => c.label)).toContain('前端維運')
    expect(result.map((c) => c.label)).not.toContain('行銷週會')
  })

  it('釘選的排在同分的前面', () => {
    const result = filterCommands(commands(), '維運')
    expect(result[0].label).toBe('前端維運')
  })

  it('開頭命中的分數高於中間命中', () => {
    const result = filterCommands(commands(), '後端')
    expect(result[0].label).toBe('後端維運')
  })

  it('也比對 Mention 的內文', () => {
    const result = filterCommands(commands(), 'timeout')
    expect(result.map((c) => c.id)).toContain('mention-47')
  })

  it('查無結果就是空陣列，不要硬湊', () => {
    expect(filterCommands(commands(), 'zzzz沒有這種東西')).toEqual([])
  })
})

describe('resolveHotkey', () => {
  it('⌘K 開命令面板，在輸入框裡也生效', () => {
    expect(resolveHotkey({ key: 'k', metaKey: true }, true)).toBe('palette')
    expect(resolveHotkey({ key: 'K', ctrlKey: true }, true)).toBe('palette')
  })

  it('⌘J 開關證據欄', () => {
    expect(resolveHotkey({ key: 'j', metaKey: true }, false)).toBe('toggle-evidence')
  })

  it('Esc 在哪裡都生效', () => {
    expect(resolveHotkey({ key: 'Escape' }, true)).toBe('escape')
  })

  it('單鍵快捷鍵在輸入框裡不生效——不然打字就變成在下指令', () => {
    expect(resolveHotkey({ key: '/' }, false)).toBe('focus-search')
    expect(resolveHotkey({ key: '/' }, true)).toBeNull()
    expect(resolveHotkey({ key: '?' }, true)).toBeNull()
  })

  it('沒對應的鍵回 null', () => {
    expect(resolveHotkey({ key: 'a' }, false)).toBeNull()
    expect(resolveHotkey({ key: 'x', metaKey: true }, false)).toBeNull()
  })
})

describe('isTypingTarget', () => {
  it('沒有 target 時不算', () => {
    expect(isTypingTarget(null)).toBe(false)
  })

  it('輸入框、文字區、可編輯區都算', () => {
    expect(isTypingTarget({ tagName: 'INPUT' } as unknown as EventTarget)).toBe(true)
    expect(isTypingTarget({ tagName: 'TEXTAREA' } as unknown as EventTarget)).toBe(true)
    expect(
      isTypingTarget({ tagName: 'DIV', isContentEditable: true } as unknown as EventTarget),
    ).toBe(true)
  })

  it('base-ui 的 combobox 會自己接管鍵盤，也算', () => {
    expect(
      isTypingTarget({
        tagName: 'BUTTON',
        getAttribute: (n: string) => (n === 'role' ? 'combobox' : null),
      } as unknown as EventTarget),
    ).toBe(true)
  })

  it('一般按鈕不算', () => {
    expect(
      isTypingTarget({ tagName: 'BUTTON', getAttribute: () => null } as unknown as EventTarget),
    ).toBe(false)
  })
})
