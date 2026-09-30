import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_LIST_SORT } from '@/lib/listSort'
import {
  listSortOf,
  resetListSortForTest,
  useListSortStore,
  viewerListSortNow,
} from '@/store/listSort'
import { useAuthStore } from '@/store/auth'
import type { Me } from '@/lib/types'

/** 最小的 Storage 替身。vitest 的 node 環境沒有 localStorage。 */
function fakeStorage() {
  const data = new Map<string, string>()
  return {
    data,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    clear: () => data.clear(),
    key: () => null,
    length: 0,
  }
}

let storage: ReturnType<typeof fakeStorage>

beforeEach(() => {
  storage = fakeStorage()
  vi.stubGlobal('localStorage', storage)
  resetListSortForTest()
})

afterEach(() => {
  vi.unstubAllGlobals()
  useAuthStore.setState({ me: null })
})

const state = () => useListSortStore.getState()

describe('排序偏好的記憶（localStorage）', () => {
  it('沒存過 → 預設值（Space 新→舊、不置頂；Mention 新→舊）', () => {
    expect(listSortOf(state(), 1)).toEqual(DEFAULT_LIST_SORT)
  })

  it('寫入後，重新整理（清掉記憶體）再讀得回來', () => {
    state().setSpaceSort(1, { sort: 'name' })
    state().setSpaceSort(1, { pinnedFirst: true })
    state().setMentionSort(1, 'sender')
    resetListSortForTest()
    expect(listSortOf(state(), 1)).toEqual({
      space: { sort: 'name', pinnedFirst: true },
      mention: 'sender',
    })
  })

  it('每位 Viewer 各記一份，不會互相繼承', () => {
    state().setSpaceSort(1, { sort: 'type', pinnedFirst: true })
    resetListSortForTest()
    expect(listSortOf(state(), 2)).toEqual(DEFAULT_LIST_SORT)
    expect(listSortOf(state(), 1).space.sort).toBe('type')
    // key 以 Viewer 區分：兩位 Viewer 寫入後是兩個不同的 key
    state().setMentionSort(2, 'time-asc')
    expect([...storage.data.keys()].sort()).toHaveLength(2)
  })

  it('改一欄不會把另一欄洗回預設', () => {
    state().setSpaceSort(1, { pinnedFirst: true })
    state().setSpaceSort(1, { sort: 'activity-asc' })
    expect(listSortOf(state(), 1).space).toEqual({ sort: 'activity-asc', pinnedFirst: true })
  })

  it('storage 讀取丟例外（隱私模式）→ 預設值，不丟到畫面', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('SecurityError')
      },
      setItem: () => {
        throw new Error('SecurityError')
      },
    })
    expect(listSortOf(state(), 7)).toEqual(DEFAULT_LIST_SORT)
    // 寫入失敗也不丟例外，而且這一輪的畫面仍然跟著換
    expect(() => state().setMentionSort(7, 'space')).not.toThrow()
    expect(listSortOf(state(), 7).mention).toBe('space')
  })

  it('存的內容壞掉 → 預設值', () => {
    storage.setItem('chatpulse.listSort.3', 'not json')
    expect(listSortOf(state(), 3)).toEqual(DEFAULT_LIST_SORT)
  })

  it('沒登入（viewerId=null）→ 預設值，而且什麼都不寫', () => {
    state().setSpaceSort(null, { sort: 'name' })
    expect(listSortOf(state(), null)).toEqual(DEFAULT_LIST_SORT)
    expect(storage.data.size).toBe(0)
  })

  it('selector 在偏好沒變時回同一個參考（否則 zustand 會無限重繪）', () => {
    expect(listSortOf(state(), 1)).toBe(listSortOf(state(), 1))
    state().setMentionSort(1, 'time-asc')
    expect(listSortOf(state(), 1)).toBe(listSortOf(state(), 1))
  })

  it('viewerListSortNow 讀的是目前登入那位 Viewer 的偏好', () => {
    state().setMentionSort(42, 'sender')
    useAuthStore.setState({ me: { viewer: { id: 42 } } as unknown as Me })
    expect(viewerListSortNow().mention).toBe('sender')
    useAuthStore.setState({ me: { viewer: { id: 43 } } as unknown as Me })
    expect(viewerListSortNow().mention).toBe('time-desc')
  })
})
