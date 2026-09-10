import { describe, expect, it } from 'vitest'
import { isOutstanding, selectMentionsByState, useMentionsStore } from './mentions'
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

  it('**手動草稿目標算在待處理裡，送出後移到已處理**', () => {
    // manual 併進 pending 計算（與後端 count_mentions 一致）：
    // 使用者按「產生回覆草稿」的意思就是「我要回這則」。
    reset([mention(99, 'manual')], { pending: 3, resolved: 15 })
    useMentionsStore.getState().applyResolved(mention(99, 'resolved'))
    expect(useMentionsStore.getState().counts).toEqual({ pending: 2, resolved: 16 })
  })

  it('**清單裡沒有的項目送出後要補進去**，否則使用者在「已處理」找不到', () => {
    reset([], { pending: 2, resolved: 15 })
    useMentionsStore.getState().applyResolved(mention(99, 'resolved'))
    const items = useMentionsStore.getState().items
    expect(items).toHaveLength(1)
    expect(items[0].id).toBe(99)
    expect(items[0].state).toBe('resolved')
  })

  it('manual 出現在「待處理」分頁，不出現在「已處理」', () => {
    const items = [mention(1, 'pending'), mention(2, 'manual'), mention(3, 'resolved')]
    expect(selectMentionsByState(items, 'pending').map((m) => m.id)).toEqual([1, 2])
    expect(selectMentionsByState(items, 'resolved').map((m) => m.id)).toEqual([3])
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

describe('isOutstanding：「什麼算待處理」的唯一定義', () => {
  it('**manual 算待處理**——收件匣按鈕該顯示「標記已處理」而不是「退回待處理」', () => {
    expect(isOutstanding('manual')).toBe(true)
  })

  it('pending 算待處理', () => {
    expect(isOutstanding('pending')).toBe(true)
  })

  it('只有 resolved 不算', () => {
    expect(isOutstanding('resolved')).toBe(false)
  })

  it('與分頁歸類同一個答案（三處判準不得再漂移）', () => {
    // 這條是守門：selectMentionsByState 與 isOutstanding 若哪天又各寫各的，
    // 「待處理分頁列出它、按鈕卻說要退回待處理」的 bug 就會重現。
    const items = [mention(1, 'pending'), mention(2, 'manual'), mention(3, 'resolved')]
    const inPendingTab = selectMentionsByState(items, 'pending').map((m) => m.id)
    expect(items.filter((m) => isOutstanding(m.state)).map((m) => m.id)).toEqual(inPendingTab)
  })
})
