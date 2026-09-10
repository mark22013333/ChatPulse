import { render } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { RouterProvider } from './useRouter'
import { useRouteSync } from './useRouteSync'
import { useMentionsStore } from '@/store/mentions'
import { useSpacesStore } from '@/store/spaces'
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
  useSpacesStore.setState({ selectedId: null })
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

/**
 * 主從切換（規格 §12）的配套規則。
 *
 * 768–1024 那個寬度下「返回清單」就是導覽到沒有 id 的位置。如果那個位置
 * 會呼叫 `select(null)`，`external`（摘要工作台建立的草稿目標）就會被清掉
 * ——而後端刻意不把那一則列進收件匣清單，使用者沒有任何路徑找得回來。
 */
describe('網址沒有指定 id ＝「不指定」，不是「忘掉剛才那個」', () => {
  it('**導覽到 #/mentions 不清掉 external**', () => {
    useMentionsStore.getState().selectExternal(mention(65))

    mountAt('#/mentions')

    expect(useMentionsStore.getState().external).not.toBeNull()
    expect(useMentionsStore.getState().selectedId).toBe(65)
  })

  it('（正對照）導覽到別的 id 仍然照清——「不指定」與「換一則」是兩件事', () => {
    useMentionsStore.getState().selectExternal(mention(65))

    mountAt('#/mentions/99')

    expect(useMentionsStore.getState().external).toBeNull()
    expect(useMentionsStore.getState().selectedId).toBe(99)
  })

  it('#/summary 不清掉選中的 Space', () => {
    useSpacesStore.setState({ selectedId: 'spaces/abc' })

    mountAt('#/summary')

    expect(useSpacesStore.getState().selectedId).toBe('spaces/abc')
  })

  it('（正對照）#/summary/:key 換成別的 Space 時照換', () => {
    useSpacesStore.setState({ selectedId: 'spaces/abc' })

    mountAt('#/summary/xyz')

    expect(useSpacesStore.getState().selectedId).toBe('spaces/xyz')
  })
})
