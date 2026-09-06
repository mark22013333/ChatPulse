import { describe, expect, it } from 'vitest'
import { useMentionsStore } from './mentions'
import type { Mention } from '@/lib/types'

function mention(id: number, state: Mention['state']): Mention {
  return {
    id,
    space_id: 'spaces/x',
    space_name: '測試',
    message_name: `spaces/x/messages/${id}`,
    thread_name: null,
    sender_display: '某人',
    create_time: '2026-09-06T10:00:00Z',
    state,
    resolved_at: state === 'resolved' ? '2026-09-06T11:00:00Z' : null,
  }
}

function reset(items: Mention[], counts: { pending: number; resolved: number }) {
  useMentionsStore.setState({ items, counts })
}

describe('applyResolved 的計數與清單', () => {
  it('待處理 -> 已處理：兩邊各動一格', () => {
    reset([mention(1, 'pending')], { pending: 3, resolved: 5 })
    useMentionsStore.getState().applyResolved(mention(1, 'resolved'))
    expect(useMentionsStore.getState().counts).toEqual({ pending: 2, resolved: 6 })
  })

  it('**手動草稿目標送出後，待處理不該被減**', () => {
    // manual 項目不在 items 裡（收件匣刻意不列它），也不算在待處理計數中。
    // 舊版只看新狀態就把 pending 減一，於是待處理平白少一個。
    reset([], { pending: 2, resolved: 15 })
    useMentionsStore.getState().applyResolved(mention(99, 'resolved'))
    expect(useMentionsStore.getState().counts).toEqual({ pending: 2, resolved: 16 })
  })

  it('**手動草稿目標送出後要補進清單**，否則使用者在「已處理」找不到', () => {
    reset([], { pending: 2, resolved: 15 })
    useMentionsStore.getState().applyResolved(mention(99, 'resolved'))
    const items = useMentionsStore.getState().items
    expect(items).toHaveLength(1)
    expect(items[0].id).toBe(99)
    expect(items[0].state).toBe('resolved')
  })

  it('已在清單裡的項目只更新、不重複加入', () => {
    reset([mention(1, 'pending'), mention(2, 'pending')], { pending: 2, resolved: 0 })
    useMentionsStore.getState().applyResolved(mention(1, 'resolved'))
    const items = useMentionsStore.getState().items
    expect(items).toHaveLength(2)
    expect(items.find((i) => i.id === 1)?.state).toBe('resolved')
  })

  it('計數不會變成負數', () => {
    reset([mention(1, 'pending')], { pending: 0, resolved: 0 })
    useMentionsStore.getState().applyResolved(mention(1, 'resolved'))
    expect(useMentionsStore.getState().counts.pending).toBe(0)
  })
})
