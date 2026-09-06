import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PREVIEW_LIMITS, threadGroups, usePreviewStore } from '@/store/preview'
import { api } from '@/lib/api'
import type { ChatMessage } from '@/lib/types'

function msg(id: string, thread: string | null, time = '2026-09-06 10:00'): ChatMessage {
  return {
    name: `spaces/S/messages/${id}`,
    sender: '某人',
    sender_id: 'users/x',
    time,
    text: id,
    thread_name: thread ? `spaces/S/threads/${thread}` : null,
  }
}

describe('threadGroups', () => {
  it('只標「這個視窗裡不只一則」的討論串', () => {
    // 私訊幾乎每則各自成一串（Google Chat 的行為）。全部都標的話整個清單
    // 都是徽章，等於沒標——這條是這個函式存在的唯一理由。
    const groups = threadGroups([msg('1', 't1'), msg('2', 't2'), msg('3', 't3')])
    expect(groups.size).toBe(0)
  })

  it('同一串的兩則會被標成同一組', () => {
    const groups = threadGroups([msg('1', 'tA'), msg('2', 'tB'), msg('3', 'tA')])
    expect(groups.size).toBe(1)
    const g = groups.get('spaces/S/threads/tA')
    expect(g?.countInWindow).toBe(2)
    expect(g?.index).toBe(1)
  })

  it('編號依照第一次出現的順序，與畫面由上而下一致', () => {
    const groups = threadGroups([
      msg('1', 'tA'),
      msg('2', 'tB'),
      msg('3', 'tB'),
      msg('4', 'tA'),
    ])
    expect(groups.get('spaces/S/threads/tA')?.index).toBe(1)
    expect(groups.get('spaces/S/threads/tB')?.index).toBe(2)
  })

  it('沒有 thread_name 的訊息不會炸掉，也不會被算進去', () => {
    const groups = threadGroups([msg('1', null), msg('2', null), msg('3', 'tA'), msg('4', 'tA')])
    expect(groups.size).toBe(1)
    expect(groups.get('spaces/S/threads/tA')?.countInWindow).toBe(2)
  })

  it('空清單回空 Map', () => {
    expect(threadGroups([]).size).toBe(0)
  })

  it('countInWindow 算的是「視窗裡」的則數，不是整串的長度', () => {
    // 這個數字會被畫面用在「展開整串（這裡只看得到 N 則）」那句話上，
    // 算錯的話那句提示就是騙人的
    const groups = threadGroups([msg('1', 'tA'), msg('2', 'tA'), msg('3', 'tA')])
    expect(groups.get('spaces/S/threads/tA')?.countInWindow).toBe(3)
  })
})

describe('PREVIEW_LIMITS', () => {
  it('就是使用者要的 10 / 20 / 30', () => {
    expect([...PREVIEW_LIMITS]).toEqual([10, 20, 30])
  })
})

describe('usePreviewStore.load', () => {
  beforeEach(() => {
    usePreviewStore.setState({
      spaceId: null,
      limit: 20,
      messages: [],
      loading: false,
      error: null,
      expanded: {},
      expanding: null,
      collapsedOverride: null,
    })
    vi.restoreAllMocks()
  })

  /** 造一個可以由測試決定何時完成的 api.messages。 */
  function deferredApi() {
    const pending: Record<string, (value: unknown) => void> = {}
    const spy = vi.spyOn(api, 'messages').mockImplementation((params) => {
      return new Promise((resolve) => {
        pending[params.space_id] = (value) => resolve(value as never)
      })
    })
    return { pending, spy }
  }

  it('換 Space 時不會被「上一個還在載入」擋掉', async () => {
    // 這是會靜默卡住的形態：A 還在載入時點 B，若連換 Space 也一起擋，
    // B 永遠不會載入，而畫面上沒有任何錯誤可看。
    const { pending, spy } = deferredApi()
    const a = usePreviewStore.getState().load('spaces/A')
    const b = usePreviewStore.getState().load('spaces/B')

    expect(spy).toHaveBeenCalledTimes(2)
    expect(spy.mock.calls.map((c) => c[0].space_id)).toEqual(['spaces/A', 'spaces/B'])

    pending['spaces/B']({ messages: [msg('b1', null)], count: 1 })
    pending['spaces/A']({ messages: [msg('a1', null)], count: 1 })
    await Promise.all([a, b])

    // 晚到的 A 不可以蓋掉 B
    expect(usePreviewStore.getState().spaceId).toBe('spaces/B')
    expect(usePreviewStore.getState().messages.map((m) => m.text)).toEqual(['b1'])
  })

  it('同一個 Space 重複呼叫只打一次 API', async () => {
    const { pending, spy } = deferredApi()
    const first = usePreviewStore.getState().load('spaces/A')
    void usePreviewStore.getState().load('spaces/A')
    expect(spy).toHaveBeenCalledTimes(1)
    pending['spaces/A']({ messages: [msg('a1', null)], count: 1 })
    await first

    // 已經有資料了，再呼叫也不重打（切頁籤回來不該再燒一次 API）
    void usePreviewStore.getState().load('spaces/A')
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('force 會重打', async () => {
    const { pending, spy } = deferredApi()
    const first = usePreviewStore.getState().load('spaces/A')
    pending['spaces/A']({ messages: [msg('a1', null)], count: 1 })
    await first
    void usePreviewStore.getState().load('spaces/A', { force: true })
    expect(spy).toHaveBeenCalledTimes(2)
  })

  it('換 Space 會清掉展開過的討論串與收合選擇', async () => {
    usePreviewStore.setState({
      spaceId: 'spaces/A',
      messages: [msg('a1', 'tA')],
      expanded: { 'spaces/A/threads/tA': [] },
      collapsedOverride: true,
    })
    const { pending } = deferredApi()
    const p = usePreviewStore.getState().load('spaces/B')
    expect(usePreviewStore.getState().expanded).toEqual({})
    expect(usePreviewStore.getState().collapsedOverride).toBeNull()
    pending['spaces/B']({ messages: [], count: 0 })
    await p
  })

  it('失敗時留下錯誤訊息而不是靜默空白', async () => {
    vi.spyOn(api, 'messages').mockRejectedValue(new Error('讀取失敗'))
    await usePreviewStore.getState().load('spaces/A')
    expect(usePreviewStore.getState().error).toBeTruthy()
    expect(usePreviewStore.getState().loading).toBe(false)
  })
})
