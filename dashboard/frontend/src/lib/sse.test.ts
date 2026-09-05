import { afterEach, describe, expect, it, vi } from 'vitest'
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
