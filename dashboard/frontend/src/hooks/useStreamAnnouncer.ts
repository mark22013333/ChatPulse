import { useEffect, useRef, useState } from 'react'
import {
  announce,
  phaseOf,
  progressAnnouncement,
  PROGRESS_INTERVAL_MS,
  type StreamOrigin,
  type StreamPhase,
  type StreamSnapshot,
} from '@/lib/streamAnnouncements'
import { hasReplyHeading, useDraftStore } from '@/store/draft'
import { useSummaryStore } from '@/store/summary'

/**
 * 串流的螢幕閱讀器宣告（設計規格 §10.6）。
 *
 * 兩個工作台都會串流，但**只需要一組 live region**——所以這個 hook 是
 * app 級的單例，由 `AppShell` 呼叫一次，內部同時盯著兩個 store。
 *
 * ### 為什麼不直接把 aria-live 掛在內容上
 *
 * `Markdown` 每個 chunk 都重寫 innerHTML。設了 `aria-live` 等於整段從頭念
 * 一次、念到一半又被下一個 chunk 打斷——比完全不宣告更糟。內容層只設
 * `aria-busy`，狀態層另開一個 `role="status"`，**只在狀態機轉換時**寫入。
 *
 * ### 訂閱哪些欄位（這是效能關鍵）
 *
 * **絕對不訂閱 `text` 與 `raw`。** 那兩個每個 chunk 都變，訂閱它們等於讓
 * 整個 App 每個 chunk 重繪一次——那正是這次改版要修掉的問題（§9.3）。
 * 這裡只訂閱布林值（`streaming`、`error`、`hasReplyHeading`），字數等到
 * 轉換發生的那一刻才用 `getState()` 讀一次。
 */
export function useStreamAnnouncer(): { polite: string; alert: string } {
  const [polite, setPolite] = useState('')
  const [alert, setAlert] = useState('')

  // 只訂閱布林。`hasReplyHeading` 只做一次 regex test、不切字串，
  // 而且結果是布林——整份串流過程中它只會翻一次，不會造成重繪。
  const summaryStreaming = useSummaryStore((s) => s.streaming)
  const summaryError = useSummaryStore((s) => s.error)
  const draftStreaming = useDraftStore((s) => s.streaming)
  const draftError = useDraftStore((s) => s.error)
  const draftReplying = useDraftStore((s) => hasReplyHeading(s.raw))

  const summaryPhase = useRef<StreamPhase>('idle')
  const draftPhase = useRef<StreamPhase>('idle')

  const emit = (origin: StreamOrigin, ref: { current: StreamPhase }, snapshot: StreamSnapshot) => {
    const next = phaseOf(snapshot)
    const said = announce(origin, ref.current, next, snapshot)
    ref.current = next
    if (!said) return
    if (said.tone === 'alert') setAlert(said.text)
    else setPolite(said.text)
  }

  useEffect(() => {
    emit('summary', summaryPhase, summarySnapshot())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summaryStreaming, summaryError])

  useEffect(() => {
    emit('draft', draftPhase, draftSnapshot())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftStreaming, draftError, draftReplying])

  /**
   * 節流層：串流中每 10 秒報一次進度。
   *
   * **不隨 chunk**——那樣一秒念好幾次，等於什麼都聽不到。兩邊同時在跑時
   * 以草稿為主（那是不可撤回動作的前置步驟，更需要進度感）。
   */
  useEffect(() => {
    if (!summaryStreaming && !draftStreaming) return
    const origin: StreamOrigin = draftStreaming ? 'draft' : 'summary'
    const startedAt = Date.now()
    const id = window.setInterval(() => {
      const seconds = Math.round((Date.now() - startedAt) / 1000)
      const chars =
        origin === 'draft'
          ? useDraftStore.getState().raw.length
          : useSummaryStore.getState().text.length
      setPolite(progressAnnouncement(origin, seconds, chars).text)
    }, PROGRESS_INTERVAL_MS)
    return () => window.clearInterval(id)
  }, [summaryStreaming, draftStreaming])

  return { polite, alert }
}

function summarySnapshot(): StreamSnapshot {
  const s = useSummaryStore.getState()
  return {
    streaming: s.streaming,
    error: s.error,
    replyStarted: false, // 摘要沒有這個階段
    charCount: s.text.length,
  }
}

function draftSnapshot(): StreamSnapshot {
  const d = useDraftStore.getState()
  return {
    streaming: d.streaming,
    error: d.error,
    replyStarted: hasReplyHeading(d.raw),
    // 完成時報**建議回話**的長度（那才是會被送出的東西）；還沒切出來時
    // 退回整份產出的長度，讓進度數字不會停在 0
    charCount: d.replyText.length || d.raw.length,
    polished: d.polish?.polished ?? null,
    polishReason: d.polish?.fallback_reason ?? null,
  }
}
