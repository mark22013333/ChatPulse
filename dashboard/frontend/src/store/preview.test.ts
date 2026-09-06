import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PREVIEW_LIMITS, buildPreviewItems, usePreviewStore } from '@/store/preview'
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

/** 把結果攤成好比對的形狀：一般訊息是它的 text，收合串是 `thread:名字(N)`。 */
function shape(items: ReturnType<typeof buildPreviewItems>) {
  return items.map((i) =>
    i.kind === 'message' ? i.message.text : `thread:${i.threadName.split('/').pop()}(${i.messages.length})`,
  )
}

describe('buildPreviewItems', () => {
  it('同一串在外層只出現一次，訊息不重複', () => {
    // 這是這個函式存在的理由。一開始外層把整串每一則都印出來、點開又再印一次，
    // 同樣的訊息在畫面上出現兩遍。
    const items = buildPreviewItems([
      msg('a1', 'tA'),
      msg('x1', null),
      msg('a2', 'tA'),
      msg('a3', 'tA'),
    ])
    expect(shape(items)).toEqual(['x1', 'thread:tA(3)'])
    // 整串的內容只掛在那一列底下
    const thread = items.find((i) => i.kind === 'thread')
    expect(thread?.kind === 'thread' && thread.messages.map((m) => m.text)).toEqual([
      'a1',
      'a2',
      'a3',
    ])
  })

  it('只收合「視窗裡不只一則」的串', () => {
    // 私訊幾乎每則各自成一串（Google Chat 的行為）。全部都收合的話整個清單
    // 都是折疊列，等於沒有清單。
    const items = buildPreviewItems([msg('1', 't1'), msg('2', 't2'), msg('3', 't3')])
    expect(shape(items)).toEqual(['1', '2', '3'])
  })

  it('收合列擺在該串「最後一則」的位置，不是第一則', () => {
    // 這個面板叫「最近訊息」——一串剛剛有人回過，就該讀起來是新的。
    const items = buildPreviewItems([
      msg('a1', 'tA'),
      msg('x1', null),
      msg('x2', null),
      msg('a2', 'tA'),
      msg('x3', null),
    ])
    expect(shape(items)).toEqual(['x1', 'x2', 'thread:tA(2)', 'x3'])
  })

  it('多串各自收合，編號依收合列的先後', () => {
    const items = buildPreviewItems([
      msg('a1', 'tA'),
      msg('b1', 'tB'),
      msg('b2', 'tB'),
      msg('a2', 'tA'),
    ])
    expect(shape(items)).toEqual(['thread:tB(2)', 'thread:tA(2)'])
    const [first, second] = items
    expect(first.kind === 'thread' && first.index).toBe(1)
    expect(second.kind === 'thread' && second.index).toBe(2)
  })

  it('沒有 thread_name 的訊息照常一則一列', () => {
    const items = buildPreviewItems([msg('1', null), msg('2', null), msg('3', 'tA'), msg('4', 'tA')])
    expect(shape(items)).toEqual(['1', '2', 'thread:tA(2)'])
  })

  it('每一則都只出現一次（不論收合與否）', () => {
    const input = [
      msg('a1', 'tA'),
      msg('x1', null),
      msg('a2', 'tA'),
      msg('b1', 'tB'),
      msg('b2', 'tB'),
    ]
    const items = buildPreviewItems(input)
    const seen = items.flatMap((i) =>
      i.kind === 'message' ? [i.message.name] : i.messages.map((m) => m.name),
    )
    expect(seen.length).toBe(input.length)
    expect(new Set(seen).size).toBe(input.length)
  })

  it('空清單回空陣列', () => {
    expect(buildPreviewItems([])).toEqual([])
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
      openThreads: [],
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

  it('換 Space 會清掉打開的討論串與收合選擇', async () => {
    usePreviewStore.setState({
      spaceId: 'spaces/A',
      messages: [msg('a1', 'tA')],
      openThreads: ['spaces/A/threads/tA'],
      expanded: { 'spaces/A/threads/tA': [] },
      collapsedOverride: true,
    })
    const { pending } = deferredApi()
    const p = usePreviewStore.getState().load('spaces/B')
    expect(usePreviewStore.getState().expanded).toEqual({})
    expect(usePreviewStore.getState().openThreads).toEqual([])
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

describe('usePreviewStore.toggleThread', () => {
  const THREAD = 'spaces/A/threads/tA'

  beforeEach(() => {
    usePreviewStore.setState({
      spaceId: 'spaces/A',
      messages: [],
      loading: false,
      error: null,
      openThreads: [],
      expanded: {},
      expanding: null,
      collapsedOverride: null,
    })
    vi.restoreAllMocks()
  })

  it('點開會立刻打開（不等 API），並去補齊整串', async () => {
    // 「立刻打開」很重要：等 API 才有反應的話，點下去像沒反應。
    // 打開的當下先用視窗裡已有的那幾則頂著（由畫面決定），這裡只驗狀態。
    let resolveFn: ((v: unknown) => void) | undefined
    const spy = vi
      .spyOn(api, 'messages')
      .mockImplementation(() => new Promise((r) => (resolveFn = r as never)))

    usePreviewStore.getState().toggleThread('spaces/A', THREAD)
    expect(usePreviewStore.getState().openThreads).toEqual([THREAD])
    expect(usePreviewStore.getState().expanding).toBe(THREAD)
    expect(spy.mock.calls[0][0].thread_name).toBe(THREAD)

    resolveFn?.({ messages: [msg('a1', 'tA'), msg('a2', 'tA')], count: 2 })
    await vi.waitFor(() => expect(usePreviewStore.getState().expanding).toBeNull())
    expect(usePreviewStore.getState().expanded[THREAD]).toHaveLength(2)
  })

  it('再點一次收起來，但抓回來的整串留在快取裡', async () => {
    vi.spyOn(api, 'messages').mockResolvedValue({
      space_id: 'spaces/A',
      space_name: 'A',
      count: 2,
      messages: [msg('a1', 'tA'), msg('a2', 'tA')],
    })
    usePreviewStore.getState().toggleThread('spaces/A', THREAD)
    await vi.waitFor(() => expect(usePreviewStore.getState().expanded[THREAD]).toBeDefined())

    usePreviewStore.getState().toggleThread('spaces/A', THREAD)
    expect(usePreviewStore.getState().openThreads).toEqual([])
    // 快取留著：再點開不必重打 API
    expect(usePreviewStore.getState().expanded[THREAD]).toHaveLength(2)
  })

  it('已經抓過的串再點開不會重打 API', async () => {
    const spy = vi.spyOn(api, 'messages').mockResolvedValue({
      space_id: 'spaces/A',
      space_name: 'A',
      count: 1,
      messages: [msg('a1', 'tA')],
    })
    usePreviewStore.getState().toggleThread('spaces/A', THREAD)
    await vi.waitFor(() => expect(usePreviewStore.getState().expanded[THREAD]).toBeDefined())
    usePreviewStore.getState().toggleThread('spaces/A', THREAD) // 收
    usePreviewStore.getState().toggleThread('spaces/A', THREAD) // 再開
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('補齊整串失敗時仍然是打開的（視窗裡那幾則還看得到）', async () => {
    vi.spyOn(api, 'messages').mockRejectedValue(new Error('讀取失敗'))
    usePreviewStore.getState().toggleThread('spaces/A', THREAD)
    await vi.waitFor(() => expect(usePreviewStore.getState().expanding).toBeNull())
    expect(usePreviewStore.getState().openThreads).toEqual([THREAD])
    expect(usePreviewStore.getState().error).toBeTruthy()
  })
})
