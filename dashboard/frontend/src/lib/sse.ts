import { ApiError } from './api'
import type { ReviewProgress, SseChunk, SseDone, SseError, SseMeta } from './types'

/**
 * 串流事件的處理函式。三個型別參數是 meta／chunk／done 各自的形狀。
 *
 * **預設值就是 Draft Reply 與摘要共用的那組**，所以既有呼叫端不用標註任何
 * 型別。工時草稿的事件形狀完全不同，呼叫時明示
 * `streamSse<WorklogSseMeta, WorklogSseChunk, WorklogSseDone>(…)`（D-17）。
 *
 * ⚠️ 被否決的做法：把工時欄位聯集進 `SseDone`。那會讓 Draft Reply 的每個
 * 欄位都多出「可能是工時的東西」，型別檢查從此什麼都擋不住。
 * `error` 的形狀是所有串流端點共用的，所以不開型別參數。
 */
export interface SseHandlers<
  M extends { type: 'meta' } = SseMeta,
  C extends { type: 'chunk' } = SseChunk,
  D extends { type: 'done' } = SseDone,
> {
  onMeta?: (event: M) => void
  onChunk?: (event: C) => void
  onDone?: (event: D) => void
  onReviewProgress?: (event: ReviewProgress) => void
  onError?: (event: SseError) => void
}

/** 解析後的一個事件。只看 `type` 分派，其餘欄位原樣交給對應的處理函式。 */
type AnySseEvent = { type: string } & Record<string, unknown>

/** 從錯誤主體取出 `error.detail`。只接受物件——字串型的 detail 是內部訊息，不該出現在這裡。 */
function pickDetail(raw: unknown): Record<string, unknown> | null {
  return raw && typeof raw === 'object' && !Array.isArray(raw) ? (raw as Record<string, unknown>) : null
}

/**
 * 把一個 SSE frame（可能多行）解析成事件物件。
 * 只取 `data:` 開頭的行，多行 data 依 SSE 規範以 `\n` 相接。
 */
function parseFrame(raw: string): AnySseEvent | null {
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
    const parsed = JSON.parse(payload) as AnySseEvent
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
export async function streamSse<
  M extends { type: 'meta' } = SseMeta,
  C extends { type: 'chunk' } = SseChunk,
  D extends { type: 'done' } = SseDone,
>(
  url: string,
  body: unknown,
  // ⚠️ `NoInfer` 不可拿掉：少了它，TS 會從 handlers 的參數型別反推 M／C／D，
  // 既有呼叫端（例如參數寫成 `(e: SseEvent) => …` 的測試）會被推成
  // `{ type: 'chunk' }` 這種骨架型別然後編譯失敗——泛型的預設值形同虛設。
  // 要換型別就明示型別參數，不靠推斷。
  handlers: SseHandlers<NoInfer<M>, NoInfer<C>, NoInfer<D>>,
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
    // 串流開始之前被擋下的 4xx，detail 是前端唯一講得出具體數字的來源
    // （例：重產守門的 `assigned_count`）。以前這裡只取 code／message，
    // 於是「會清掉幾筆」只剩從中文句子裡挖這條路。
    let detail: Record<string, unknown> | null = null
    try {
      const payload = (await res.json()) as {
        error?: { code?: string; message?: string; detail?: unknown }
      }
      if (payload?.error) {
        code = payload.error.code ?? code
        message = payload.error.message ?? message
        detail = pickDetail(payload.error.detail)
      }
    } catch {
      // 非 JSON 回應，沿用預設訊息
    }
    if (res.status === 401) throw new ApiError(401, code, message, null, detail)
    // detail 為 null 時整個鍵不放，讓既有呼叫端收到的事件形狀與以前一模一樣
    handlers.onError?.(detail ? { type: 'error', code, message, detail } : { type: 'error', code, message })
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

  // ⚠️ 這裡的轉型是信任後端的形狀：事件只依 `type` 分派，其餘欄位不驗。
  // 與改成泛型之前的行為相同（以前是 `as SseEvent`），不是新開的洞。
  const dispatch = (event: AnySseEvent): boolean => {
    switch (event.type) {
      case 'meta':
        handlers.onMeta?.(event as unknown as M)
        return false
      case 'chunk':
        handlers.onChunk?.(event as unknown as C)
        return false
      case 'review_progress':
        handlers.onReviewProgress?.(event as unknown as ReviewProgress)
        return false
      case 'done':
        handlers.onDone?.(event as unknown as D)
        return true
      case 'error':
        handlers.onError?.(event as unknown as SseError)
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
