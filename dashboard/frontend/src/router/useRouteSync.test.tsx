import { render } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { RouterProvider } from './useRouter'
import { useRouteSync } from './useRouteSync'
import { useMentionsStore } from '@/store/mentions'
import type { Mention } from '@/lib/types'

function mention(id: number): Mention {
  return {
    id,
    space_id: 'spaces/x',
    space_name: '（未命名空間）',
    message_name: `spaces/x/messages/${id}`,
    thread_name: null,
    sender_display: '陳柏元',
    create_time: '2026-09-10T02:16:39Z',
    state: 'manual',
    resolved_at: null,
  }
}

function Sync() {
  useRouteSync()
  return null
}

function mountAt(hash: string) {
  window.history.replaceState(null, '', hash)
  return render(
    <RouterProvider>
      <Sync />
    </RouterProvider>,
  )
}

beforeEach(() => {
  useMentionsStore.setState({ items: [], selectedId: null, mergeIds: [], external: null })
})

describe('useRouteSync：?merge= → 勾選狀態', () => {
  it('**貼一條帶 merge 的網址進來，勾選還原得回來**', () => {
    mountAt('#/mentions/47?merge=47,48')

    expect(useMentionsStore.getState().mergeIds).toEqual([47, 48])
    expect(useMentionsStore.getState().selectedId).toBe(47)
  })

  it('順序照網址寫的來（第一個是主要那則）', () => {
    mountAt('#/mentions/48?merge=48,47')

    expect(useMentionsStore.getState().mergeIds).toEqual([48, 47])
  })

  it('網址上沒有 merge、store 裡是真的合併 → 清掉（按返回鍵要回得去）', () => {
    useMentionsStore.setState({ mergeIds: [47, 48] })

    mountAt('#/mentions/47')

    expect(useMentionsStore.getState().mergeIds).toEqual([])
  })

  it('**網址上沒有 merge、store 裡只勾了一則 → 不要動它**', () => {
    // hashForMentions 兩則以上才帶 merge，所以「只勾一則」這個過渡狀態
    // 網址根本表達不了。照樣清掉的話，使用者勾第一則的瞬間它會自己彈回去。
    useMentionsStore.setState({ mergeIds: [47] })

    mountAt('#/mentions/47')

    expect(useMentionsStore.getState().mergeIds).toEqual([47])
  })
})

describe('useRouteSync 的去重（紅線 4）', () => {
  it('**selectedId 已經對上時不再呼叫 select()，external 才不會被清掉**', () => {
    // 摘要工作台按「產生回覆草稿」走的就是這條：先 selectExternal()（它自己
    // 會設好 selectedId）再導航。少了這個去重，切過去的瞬間 select() 會把
    // external 清成 null，工作區變成空白。
    useMentionsStore.getState().selectExternal(mention(65))
    expect(useMentionsStore.getState().external).not.toBeNull()

    mountAt('#/mentions/65')

    expect(useMentionsStore.getState().external).not.toBeNull()
    expect(useMentionsStore.getState().selectedId).toBe(65)
  })

  it('正對照：換成別則時 select() 真的會跑，external 被清掉', () => {
    useMentionsStore.getState().selectExternal(mention(65))

    mountAt('#/mentions/99')

    expect(useMentionsStore.getState().external).toBeNull()
    expect(useMentionsStore.getState().selectedId).toBe(99)
  })
})
