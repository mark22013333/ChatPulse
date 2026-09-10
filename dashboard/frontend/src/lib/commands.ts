import { hashForMentions, hashForSettings, hashForSummary } from '@/lib/route'
import type { Mention, Space } from '@/lib/types'

/**
 * 命令面板的資料層（設計規格 §11）。純函式，node 可測。
 *
 * **送出回話與推播不在這裡**，這是刻意的：不可撤回的動作不該有肌肉記憶，
 * 而命令面板「打字 → Enter」正是最容易誤觸的介面。
 */

export interface Command {
  id: string
  /** 顯示的主要文字 */
  label: string
  /** 次要說明；Space 用它放類型與最後活動 */
  hint?: string
  group: '前往' | 'Space' | 'Mention' | '設定'
  /** 執行＝導航到這個 hash */
  href: string
  /** 已釘選的 Space 排前面 */
  pinned?: boolean
}

const NAV_COMMANDS: Command[] = [
  { id: 'go-summary', label: '摘要工作台', group: '前往', href: hashForSummary() },
  { id: 'go-mentions', label: 'Mention 收件匣', group: '前往', href: hashForMentions() },
  { id: 'go-settings', label: '設定', group: '前往', href: hashForSettings('reply') },
  {
    id: 'go-diagnostics',
    label: '診斷',
    hint: '服務狀態、採集器、潤稿規則版本',
    group: '前往',
    href: hashForSettings('diagnostics'),
  },
]

const SETTINGS_COMMANDS: Command[] = [
  { id: 'set-reply', label: '回覆預設值', group: '設定', href: hashForSettings('reply') },
  { id: 'set-personas', label: 'Persona', group: '設定', href: hashForSettings('personas') },
  { id: 'set-prompts', label: '常用提示詞', group: '設定', href: hashForSettings('prompts') },
  {
    id: 'set-code',
    label: '參考專案',
    group: '設定',
    href: hashForSettings('code-projects'),
  },
  { id: 'set-spaces', label: 'Space 釘選', group: '設定', href: hashForSettings('spaces') },
  { id: 'set-data', label: '用量與歷史', group: '設定', href: hashForSettings('data') },
]

export function buildCommands(input: {
  spaces: Space[]
  mentions: Mention[]
  spaceTypeLabel: (type: string | null | undefined) => string
  relativeTime: (iso: string | null | undefined) => string
}): Command[] {
  const { spaces, mentions, spaceTypeLabel, relativeTime } = input

  const spaceCommands: Command[] = spaces.map((space) => ({
    id: `space-${space.id}`,
    label: space.displayName || space.id,
    hint: `${spaceTypeLabel(space.type)}　最後活動 ${relativeTime(space.lastActiveTime)}`,
    group: 'Space',
    href: hashForSummary(space.id),
    pinned: space.pinned,
  }))

  const mentionCommands: Command[] = mentions.map((mention) => ({
    id: `mention-${mention.id}`,
    label: `${mention.sender_display} · ${mention.space_name}`,
    hint: (mention.text ?? '').slice(0, 60).replace(/\n/g, ' '),
    group: 'Mention',
    href: hashForMentions(mention.id),
  }))

  return [...NAV_COMMANDS, ...SETTINGS_COMMANDS, ...mentionCommands, ...spaceCommands]
}

/**
 * 過濾與排序。
 *
 * 436 個 Space 全部丟進面板是刻意的——`⌘K` 的價值就在於「不必先找到清單、
 * 不必捲動」。但沒有輸入關鍵字時只給前幾筆，不然一打開就是一面牆。
 */
export function filterCommands(commands: Command[], query: string, limit = 12): Command[] {
  const keyword = query.trim().toLowerCase()

  if (!keyword) {
    const nav = commands.filter((c) => c.group === '前往')
    const pinned = commands.filter((c) => c.pinned)
    const mentions = commands.filter((c) => c.group === 'Mention').slice(0, 5)
    return [...nav, ...pinned, ...mentions].slice(0, limit)
  }

  const scored = commands
    .map((command) => ({ command, score: score(command, keyword) }))
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score)

  return scored.slice(0, limit).map((entry) => entry.command)
}

function score(command: Command, keyword: string): number {
  const label = command.label.toLowerCase()
  const hint = (command.hint ?? '').toLowerCase()

  let base = 0
  if (label.startsWith(keyword)) base = 100
  else if (label.includes(keyword)) base = 60
  else if (hint.includes(keyword)) base = 20
  if (base === 0) return 0

  // 導覽類永遠優先（它們是「我想去哪」，命中時通常就是意圖本身）
  if (command.group === '前往') base += 30
  if (command.pinned) base += 15
  return base
}
