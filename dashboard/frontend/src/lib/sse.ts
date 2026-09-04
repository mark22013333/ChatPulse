import { ApiError } from './api'
import type { SseChunk, SseDone, SseError, SseEvent, SseMeta } from './types'

export interface SseHandlers {
  onMeta?: (event: SseMeta) => void
  onChunk?: (event: SseChunk) => void
  onDone?: (event: SseDone) => void
  onError?: (event: SseError) => void
}

/**
 * 把一個 SSE frame（可能多行）解析成事件物件。
 * 只取 `data:` 開頭的行，多行 data 依 SSE 規範以 `\n` 相接。
 */
function parseFrame(raw: string): SseEvent | null {
  const dataLines: string[] = []
  for (const line of raw.split('\n')) {
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).replace(/^ /, ''))
    }
  }
  if (dataLines.length === 0) return null
  const payload = dataLines.join('\n').trim()
  if (!payload || payload === '[DONE]') return null
  try {
    const parsed = JSON.parse(payload) as SseEvent
    return parsed && typeof parsed.type === 'string' ? parsed : null
  } catch {
    // 半個 frame 或非 JSON 的心跳，忽略即可
    return null
  }
}

/**
 * 以 fetch + ReadableStream 消費後端的 POST SSE 端點。
 * 契約明訂不可用瀏覽器內建的事件串流 API——它只發得出 GET，而這些端點是 POST。
 *
 * 關鍵行為：
 * 1. 維護 buffer，只有看到完整的 `\n\n` 才切出 frame，殘料留到下一個 chunk——
 *    網路 chunk 的邊界隨時可能切在 frame 中間。
 * 2. 收到 `done` 或 `error` 立刻停止讀取並 cancel reader。
 * 3. 呼叫端以 AbortController 中止（元件 unmount／切換目標）。
 * 4. HTTP 非 2xx 時後端還來得及回 JSON 錯誤，轉成 error 事件交給呼叫端顯示。
 */
export async function streamSse(
  url: string,
  body: unknown,
  handlers: SseHandlers,
  signal: AbortSignal,
): Promise<void> {
  let res: Response
  try {
    res = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
      signal,
    })
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') return
    handlers.onError?.({
      type: 'error',
      code: 'NETWORK_ERROR',
      message: '無法連線到伺服器，請確認後端服務是否啟動',
    })
    return
  }

  if (!res.ok) {
    let code = `HTTP_${res.status}`
    let message = `串流建立失敗（HTTP ${res.status}）`
    try {
      const payload = (await res.json()) as { error?: { code?: string; message?: string } }
      if (payload?.error) {
        code = payload.error.code ?? code
        message = payload.error.message ?? message
      }
    } catch {
      // 非 JSON 回應，沿用預設訊息
    }
    if (res.status === 401) throw new ApiError(401, code, message)
    handlers.onError?.({ type: 'error', code, message })
    return
  }

  if (!res.body) {
    handlers.onError?.({ type: 'error', code: 'NO_STREAM_BODY', message: '伺服器沒有回傳串流內容' })
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let finished = false

  const dispatch = (event: SseEvent): boolean => {
    switch (event.type) {
      case 'meta':
        handlers.onMeta?.(event)
        return false
      case 'chunk':
        handlers.onChunk?.(event)
        return false
      case 'done':
        handlers.onDone?.(event)
        return true
      case 'error':
        handlers.onError?.(event)
        return true
      default:
        return false
    }
  }

  const drain = (): void => {
    let index = buffer.indexOf('\n\n')
    while (index !== -1) {
      const raw = buffer.slice(0, index)
      buffer = buffer.slice(index + 2)
      const event = parseFrame(raw)
      if (event && dispatch(event)) {
        finished = true
        return
      }
      index = buffer.indexOf('\n\n')
    }
  }

  try {
    while (!finished) {
      const { value, done } = await reader.read()
      if (done) break
      // 先併進 buffer，再對 buffer 整體正規化換行。
      // 不可以逐 chunk 做 replace：chunk 若恰好切在 \r 與 \n 之間，
      // 兩個 chunk 各自都看不到完整的 \r\n，frame 邊界就被漏掉、掉一個事件。
      // 目前後端只送 \n，但中間若經過會改寫換行的 proxy 就會踩到。
      buffer += decoder.decode(value, { stream: true })
      buffer = buffer.replace(/\r\n/g, '\n')
      drain()
    }
    if (!finished) {
      // 串流自然結束但殘料還沒有結尾的空行，補送一次
      buffer += decoder.decode()
      buffer += '\n\n'
      drain()
    }
  } catch (err) {
    if ((err as Error)?.name !== 'AbortError') {
      handlers.onError?.({
        type: 'error',
        code: 'STREAM_INTERRUPTED',
        message: `串流中斷：${(err as Error)?.message ?? '未知原因'}`,
      })
    }
  } finally {
    try {
      await reader.cancel()
    } catch {
      // reader 已關閉，忽略
    }
  }
}
