import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './api'
import { streamSse } from './sse'
import type { SseEvent } from './types'

/** 把一串「刻意切壞」的字串片段做成 SSE 回應。 */
function mockStream(pieces: string[], status = 200): void {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const piece of pieces) controller.enqueue(encoder.encode(piece))
      controller.close()
    },
  })
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      status === 200
        ? new Response(body, { status, headers: { 'Content-Type': 'text/event-stream' } })
        : new Response(JSON.stringify({ error: { code: 'INVALID_PARAMETER', message: '抓取則數超出範圍' } }), {
            status,
            headers: { 'Content-Type': 'application/json' },
          }),
    ),
  )
}

function collect() {
  const events: SseEvent[] = []
  return {
    events,
    handlers: {
      onMeta: (e: SseEvent) => events.push(e),
      onChunk: (e: SseEvent) => events.push(e),
      onDone: (e: SseEvent) => events.push(e),
      onError: (e: SseEvent) => events.push(e),
    },
  }
}

afterEach(() => vi.unstubAllGlobals())

describe('streamSse', () => {
  it('把切在 frame 中間的 chunk 重新拼回完整事件', async () => {
    // 第 1 段結束在 JSON 中間；第 2 段補完並帶出下一個 frame 的開頭
    mockStream([
      'data: {"type":"meta","space":"0.暫存","mes',
      'sage_count":50}\n\ndata: {"type":"chunk","te',
      'xt":"本週討論"}\n\ndata: {"type":"chunk","text":"集中在"}\n\n',
      'data: {"type":"done","summary_id":12}\n\n',
    ])
    const { events, handlers } = collect()
    await streamSse('/api/v1/summarize/stream', {}, handlers, new AbortController().signal)

    expect(events.map((e) => e.type)).toEqual(['meta', 'chunk', 'chunk', 'done'])
    expect(events.filter((e) => e.type === 'chunk').map((e) => (e as { text: string }).text).join('')).toBe(
      '本週討論集中在',
    )
    expect((events[3] as { summary_id: number }).summary_id).toBe(12)
  })

  it('查核進度按順序交給呼叫端，完成後不再接收進度', async () => {
    mockStream([
      'data: {"type":"meta","mention_id":7}\n\n',
      'data: {"type":"review_progress","stage":"reading","mention_id":7,"scope_digest":"scope","counts":{"reads":1}}\n\n',
      'data: {"type":"done","draft_id":9}\n\n',
      'data: {"type":"review_progress","stage":"reviewing","mention_id":7,"scope_digest":"scope","counts":{}}\n\n',
    ])
    const events: string[] = []
    await streamSse('/x', {}, {
      onMeta: () => events.push('meta'),
      onReviewProgress: (event) => events.push(event.stage),
      onDone: () => events.push('done'),
    }, new AbortController().signal)
    expect(events).toEqual(['meta', 'reading', 'done'])
  })

  it('一個 chunk 內含多個 frame 也要全部取出', async () => {
    mockStream([
      'data: {"type":"chunk","text":"A"}\n\ndata: {"type":"chunk","text":"B"}\n\ndata: {"type":"chunk","text":"C"}\n\ndata: {"type":"done"}\n\n',
    ])
    const { events, handlers } = collect()
    await streamSse('/x', {}, handlers, new AbortController().signal)
    expect(events.map((e) => e.type)).toEqual(['chunk', 'chunk', 'chunk', 'done'])
  })

  it('容忍 \\r\\n\\r\\n 的 frame 邊界', async () => {
    mockStream(['data: {"type":"chunk","text":"X"}\r\n\r\ndata: {"type":"done"}\r\n\r\n'])
    const { events, handlers } = collect()
    await streamSse('/x', {}, handlers, new AbortController().signal)
    expect(events.map((e) => e.type)).toEqual(['chunk', 'done'])
  })

  it('收到 error 事件後停止讀取，後面的 frame 不再送出', async () => {
    mockStream([
      'data: {"type":"chunk","text":"A"}\n\n',
      'data: {"type":"error","code":"GEMINI_QUOTA_EXCEEDED","message":"Gemini 配額已用盡"}\n\n',
      'data: {"type":"chunk","text":"不該出現"}\n\n',
    ])
    const { events, handlers } = collect()
    await streamSse('/x', {}, handlers, new AbortController().signal)
    expect(events.map((e) => e.type)).toEqual(['chunk', 'error'])
    expect((events[1] as { code: string }).code).toBe('GEMINI_QUOTA_EXCEEDED')
  })

  it('沒有結尾空行時仍會送出最後一個 frame', async () => {
    mockStream(['data: {"type":"chunk","text":"尾巴"}\n\ndata: {"type":"done"}'])
    const { events, handlers } = collect()
    await streamSse('/x', {}, handlers, new AbortController().signal)
    expect(events.map((e) => e.type)).toEqual(['chunk', 'done'])
  })

  it('HTTP 400 時把後端的錯誤碼與訊息轉成 error 事件', async () => {
    mockStream([], 400)
    const { events, handlers } = collect()
    await streamSse('/x', {}, handlers, new AbortController().signal)
    expect(events).toHaveLength(1)
    expect(events[0]).toMatchObject({ type: 'error', code: 'INVALID_PARAMETER' })
  })

  it('AbortController.abort() 之後不再回報事件', async () => {
    const controller = new AbortController()
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init: RequestInit) => {
        init.signal?.throwIfAborted()
        const error = new Error('aborted')
        error.name = 'AbortError'
        throw error
      }),
    )
    controller.abort()
    const { events, handlers } = collect()
    await streamSse('/x', {}, handlers, controller.signal)
    expect(events).toHaveLength(0)
  })
})

/** 非 2xx 的 JSON 錯誤回應，`error` 內容由測試決定。 */
function mockErrorBody(status: number, error: Record<string, unknown>): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ error }), { status, headers: { 'Content-Type': 'application/json' } }),
    ),
  )
}

describe('streamSse：錯誤主體的 detail（D-17）', () => {
  it('串流開始前被擋下的 400，detail 物件原樣帶到 error 事件', async () => {
    // 重產守門：前端要講得出「會清掉幾筆」，數字只能從這裡來
    mockErrorBody(400, {
      code: 'INVALID_PARAMETER',
      message: '有 2 筆已經選好專案了',
      detail: { assigned_count: 2, assigned_issue_keys: ['PEI-133', 'PEI-140'], force_allowed: true },
    })
    const { events, handlers } = collect()
    await streamSse('/api/v1/worklogs/2026-09-13/draft/stream', {}, handlers, new AbortController().signal)
    expect(events).toHaveLength(1)
    expect(events[0]).toMatchObject({ type: 'error', code: 'INVALID_PARAMETER' })
    expect((events[0] as { detail?: Record<string, unknown> }).detail).toEqual({
      assigned_count: 2,
      assigned_issue_keys: ['PEI-133', 'PEI-140'],
      force_allowed: true,
    })
  })

  it('409 的 detail 也帶得出來', async () => {
    mockErrorBody(409, {
      code: 'WORKLOG_ALREADY_SUBMITTED',
      message: '已經送出了',
      detail: { submitted_count: 1, force_allowed: false },
    })
    const { events, handlers } = collect()
    await streamSse('/x', {}, handlers, new AbortController().signal)
    expect(events[0]).toMatchObject({ code: 'WORKLOG_ALREADY_SUBMITTED', detail: { submitted_count: 1 } })
  })

  it('沒有 detail 時事件不多出 detail 鍵（既有呼叫端收到的形狀不變）', async () => {
    mockErrorBody(400, { code: 'INVALID_PARAMETER', message: '抓取則數超出範圍' })
    const { events, handlers } = collect()
    await streamSse('/x', {}, handlers, new AbortController().signal)
    expect(events[0]).toMatchObject({ type: 'error', code: 'INVALID_PARAMETER' })
    expect('detail' in events[0]).toBe(false)
  })

  it('字串或陣列型的 detail 不外洩成結構化 detail', async () => {
    for (const detail of ['內部堆疊', ['a', 'b']]) {
      mockErrorBody(400, { code: 'INVALID_PARAMETER', message: 'x', detail })
      const { events, handlers } = collect()
      await streamSse('/x', {}, handlers, new AbortController().signal)
      expect('detail' in events[0]).toBe(false)
    }
  })

  it('401 拋出的 ApiError 也帶著 detail', async () => {
    mockErrorBody(401, { code: 'UNAUTHENTICATED', message: '請重新登入', detail: { reason: 'expired' } })
    const { handlers } = collect()
    const err = await streamSse('/x', {}, handlers, new AbortController().signal).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(401)
    expect((err as ApiError).detail).toEqual({ reason: 'expired' })
  })

  it('串流中途的 error 事件若自帶 detail，原樣交給 onError', async () => {
    mockStream([
      'data: {"type":"meta","work_date":"2026-09-13"}\n\n',
      'data: {"type":"error","code":"WORKLOG_ALREADY_SUBMITTED","message":"x","detail":{"submitted_count":3}}\n\n',
    ])
    const { events, handlers } = collect()
    await streamSse('/x', {}, handlers, new AbortController().signal)
    expect(events.map((e) => e.type)).toEqual(['meta', 'error'])
    expect((events[1] as { detail?: Record<string, unknown> }).detail).toEqual({ submitted_count: 3 })
  })
})

describe('streamSse：工時草稿的事件形狀', () => {
  it('meta／chunk／done 依序分派，工時專屬欄位原樣保留', async () => {
    mockStream([
      'data: {"type":"meta","work_date":"2026-09-13","clusters":[{"key":"c-a","label":"匯出逾時"}]}\n\n',
      'data: {"type":"chunk","index":0,"cluster_key":"c-a","text":"修正匯出逾時"}\n\n',
      'data: {"type":"done","draft_id":7,"entries":[],"allocation":null}\n\n',
    ])
    const { events, handlers } = collect()
    await streamSse('/api/v1/worklogs/2026-09-13/draft/stream', { tz_offset: '+08:00' }, handlers, new AbortController().signal)
    expect(events.map((e) => e.type)).toEqual(['meta', 'chunk', 'done'])
    expect((events[1] as unknown as { cluster_key: string }).cluster_key).toBe('c-a')
    expect((events[2] as unknown as { draft_id: number }).draft_id).toBe(7)
  })
})
