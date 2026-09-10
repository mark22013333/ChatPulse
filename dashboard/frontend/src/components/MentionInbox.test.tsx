import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MentionInbox } from './MentionInbox'
import { useMentionsStore } from '@/store/mentions'
import { useSpacesStore } from '@/store/spaces'
import type { Mention, MentionStateValue } from '@/lib/types'

function mention(id: number, state: MentionStateValue): Mention {
  return {
    id,
    space_id: 'spaces/x',
    space_name: '（未命名空間）',
    message_name: `spaces/x/messages/${id}`,
    thread_name: null,
    sender_display: '陳柏元',
    create_time: '2026-09-10T02:16:39Z',
    state,
    resolved_at: state === 'resolved' ? '2026-09-10T03:00:00Z' : null,
  }
}

/** 找到某則 Mention 那張卡片，斷言限縮在卡片內——清單裡每則都有一顆同名按鈕。 */
function card(sender: string) {
  return screen.getByText(`${sender} 提到你`).closest('li') as HTMLElement
}

function seed(items: Mention[], tab: 'pending' | 'resolved' = 'pending') {
  useMentionsStore.setState({
    items,
    tab,
    counts: { pending: items.filter((m) => m.state !== 'resolved').length, resolved: 0 },
    loading: false,
    checking: false,
    error: null,
    selectedId: null,
    mergeIds: [],
  })
  useSpacesStore.setState({ items: [] })
}

beforeEach(() => {
  seed([])
})

describe('MentionInbox 的狀態按鈕', () => {
  it('**manual（摘要工作台挑的草稿目標）顯示「標記已處理」**，不是「退回待處理」', () => {
    // 迴歸：manual 在待處理分頁列得出來，按鈕卻寫「退回待處理」；按下去會把
    // state 改成 pending，無聲抹掉「自選對話」這個來源標記，而且不可逆。
    seed([mention(65, 'manual')])
    render(<MentionInbox onSelect={() => {}} onMergedGenerate={() => {}} />)

    const row = card('陳柏元')
    expect(within(row).getByRole('button', { name: '標記已處理' })).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: '退回待處理' })).toBeNull()
  })

  it('pending 一樣顯示「標記已處理」', () => {
    seed([mention(1, 'pending')])
    render(<MentionInbox onSelect={() => {}} onMergedGenerate={() => {}} />)

    expect(
      within(card('陳柏元')).getByRole('button', { name: '標記已處理' }),
    ).toBeInTheDocument()
  })

  it('已處理分頁才是「退回待處理」（正對照：這個文案本身沒有消失）', () => {
    seed([mention(9, 'resolved')], 'resolved')
    render(<MentionInbox onSelect={() => {}} onMergedGenerate={() => {}} />)

    const row = card('陳柏元')
    expect(within(row).getByRole('button', { name: '退回待處理' })).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: '標記已處理' })).toBeNull()
  })

  it('**按 manual 的「標記已處理」送出的是 resolved**，不是 pending', async () => {
    seed([mention(65, 'manual')])
    const setMentionState = vi.fn().mockResolvedValue(undefined)
    useMentionsStore.setState({ setMentionState })
    render(<MentionInbox onSelect={() => {}} onMergedGenerate={() => {}} />)

    await userEvent.click(within(card('陳柏元')).getByRole('button', { name: '標記已處理' }))

    expect(setMentionState).toHaveBeenCalledWith(65, 'resolved')
  })

  it('manual 出現在待處理分頁、不出現在已處理分頁', () => {
    seed([mention(65, 'manual')], 'resolved')
    render(<MentionInbox onSelect={() => {}} onMergedGenerate={() => {}} />)

    expect(screen.queryByText('陳柏元 提到你')).toBeNull()
    expect(screen.getByText('還沒有已處理的 Mention。')).toBeInTheDocument()
  })
})
