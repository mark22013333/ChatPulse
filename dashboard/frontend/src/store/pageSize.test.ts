import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  pageSizeOf,
  parsePageSizePrefs,
  resetPageSizeForTest,
  usePageSizeStore,
} from '@/store/pageSize'
import { useAuthStore } from '@/store/auth'

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
  resetPageSizeForTest()
})

afterEach(() => {
  vi.unstubAllGlobals()
  useAuthStore.setState({ me: null })
})

const state = () => usePageSizeStore.getState()

describe('每頁筆數偏好（localStorage）', () => {
  it('沒存過 → 25', () => {
    expect(pageSizeOf(state(), 1, 'summary')).toBe(25)
  })

  it('寫入後，重新整理（清掉記憶體）再讀得回來', () => {
    state().setPageSize(1, 'summary', 50)
    resetPageSizeForTest()
    expect(pageSizeOf(state(), 1, 'summary')).toBe(50)
  })

  it('每張表格各記一份：改摘要工作台不影響工時草稿', () => {
    state().setPageSize(1, 'summary', 10)
    state().setPageSize(1, 'worklog', 50)
    resetPageSizeForTest()
    expect(pageSizeOf(state(), 1, 'summary')).toBe(10)
    expect(pageSizeOf(state(), 1, 'worklog')).toBe(50)
  })

  it('每位 Viewer 各記一份，不會互相繼承', () => {
    state().setPageSize(1, 'summary', 10)
    resetPageSizeForTest()
    expect(pageSizeOf(state(), 2, 'summary')).toBe(25)
    state().setPageSize(2, 'summary', 50)
    expect([...storage.data.keys()].sort()).toEqual(['chatpulse.pageSize.1', 'chatpulse.pageSize.2'])
  })

  it('storage 丟例外（隱私模式）→ 25，寫入也不丟例外，這一輪畫面仍跟著換', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('SecurityError')
      },
      setItem: () => {
        throw new Error('SecurityError')
      },
    })
    expect(pageSizeOf(state(), 7, 'summary')).toBe(25)
    expect(() => state().setPageSize(7, 'summary', 10)).not.toThrow()
    expect(pageSizeOf(state(), 7, 'summary')).toBe(10)
  })

  it('沒登入（viewerId=null）→ 25，而且什麼都不寫', () => {
    state().setPageSize(null, 'summary', 50)
    expect(pageSizeOf(state(), null, 'summary')).toBe(25)
    expect(storage.data.size).toBe(0)
  })

  it('selector 在偏好沒變時回同一個值（數字），不會引起重繪迴圈', () => {
    expect(pageSizeOf(state(), 1, 'worklog')).toBe(pageSizeOf(state(), 1, 'worklog'))
  })
})

describe('parsePageSizePrefs：壞掉的內容只退那一欄', () => {
  it('不是 JSON／不是物件 → 全部預設', () => {
    expect(parsePageSizePrefs('not json')).toEqual({})
    expect(parsePageSizePrefs('[50]')).toEqual({})
    expect(parsePageSizePrefs('null')).toEqual({})
  })

  it('⚠️ 0、字串、清單外的數字都不收；合法的那欄保留；認不得的表格 id 丟掉', () => {
    expect(parsePageSizePrefs(JSON.stringify({ summary: 0, worklog: 50, other: 10 }))).toEqual({
      worklog: 50,
    })
    expect(parsePageSizePrefs(JSON.stringify({ summary: 'abc', worklog: 20 }))).toEqual({})
  })

  it('壞掉的內容讀進 store → 那張表用 25', () => {
    storage.setItem('chatpulse.pageSize.3', JSON.stringify({ summary: 0 }))
    expect(pageSizeOf(state(), 3, 'summary')).toBe(25)
  })
})
