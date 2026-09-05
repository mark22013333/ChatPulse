import { create } from 'zustand'
import { api, errorMessage, LIMIT_DEFAULT, streamUrls } from '@/lib/api'
import { streamSse } from '@/lib/sse'
import { streamErrorMessage } from '@/lib/aiErrors'
import { providerRequestField } from '@/store/providers'
import type { Mention, SseMeta } from '@/lib/types'

/** 建議回話段落的標題（契約：`### ✍️ 建議回話`）。容忍 emoji 與空白差異。 */
const REPLY_HEADING = /^#{2,4}\s*.*建議回話.*$/m
/** 脈絡分析段落的標題（契約：`### 🧭 脈絡分析`）。 */
const CONTEXT_HEADING = /^#{2,4}\s*.*脈絡分析.*$/m

export interface DraftSections {
  context: string
  reply: string
  /** 建議回話段落的標題是否已經串流出來 */
  replyStarted: boolean
}

/** 把串流中的 Markdown 即時切成「脈絡分析」與「建議回話」兩段。 */
export function splitDraft(raw: string): DraftSections {
  if (!raw) return { context: '', reply: '', replyStarted: false }
  const replyMatch = REPLY_HEADING.exec(raw)
  if (!replyMatch) {
    return { context: stripContextHeading(raw), reply: '', replyStarted: false }
  }
  const head = raw.slice(0, replyMatch.index)
  const tail = raw.slice(replyMatch.index + replyMatch[0].length)
  return { context: stripContextHeading(head), reply: tail.replace(/^\n+/, ''), replyStarted: true }
}

function stripContextHeading(text: string): string {
  const match = CONTEXT_HEADING.exec(text)
  if (!match) return text.trim()
  return text.slice(match.index + match[0].length).replace(/^\n+/, '')
}

interface DraftState {
  /** 勾選的 Reference Space —— 規格 7.3：預設一個都不勾 */
  referenceSpaceIds: string[]
  referenceSearch: string
  refLimit: number
  refLimitError: string | null

  streaming: boolean
  raw: string
  meta: SseMeta | null
  draftId: number | null
  error: string | null
  /** 這份草稿屬於哪一則 Mention */
  mentionId: number | null

  /** 行內編輯器的內容 */
  replyText: string
  /** 使用者動過編輯器之後就不再被串流覆寫 */
  replyEdited: boolean

  sending: boolean

  toggleReference: (spaceId: string) => void
  clearReferences: () => void
  setReferenceSearch: (value: string) => void
  setRefLimit: (raw: string) => void
  setReplyText: (text: string) => void
  generate: (mentionId: number) => Promise<void>
  abort: () => void
  reset: () => void
  send: (mentionId: number) => Promise<Mention | null>
}

let controller: AbortController | null = null

export const useDraftStore = create<DraftState>((set, get) => ({
  referenceSpaceIds: [],
  referenceSearch: '',
  refLimit: LIMIT_DEFAULT,
  refLimitError: null,

  streaming: false,
  raw: '',
  meta: null,
  draftId: null,
  error: null,
  mentionId: null,

  replyText: '',
  replyEdited: false,

  sending: false,

  toggleReference: (spaceId) =>
    set((state) => ({
      referenceSpaceIds: state.referenceSpaceIds.includes(spaceId)
        ? state.referenceSpaceIds.filter((id) => id !== spaceId)
        : [...state.referenceSpaceIds, spaceId],
    })),

  clearReferences: () => set({ referenceSpaceIds: [] }),
  setReferenceSearch: (value) => set({ referenceSearch: value }),

  setRefLimit: (raw) => {
    const trimmed = raw.trim()
    if (trimmed === '') {
      set({ refLimit: Number.NaN, refLimitError: '請輸入抓取則數' })
      return
    }
    const parsed = Number(trimmed)
    if (!Number.isInteger(parsed)) {
      set({ refLimit: Number.NaN, refLimitError: '抓取則數必須是整數' })
      return
    }
    if (parsed < 1 || parsed > 1000) {
      set({ refLimit: parsed, refLimitError: '抓取則數必須介於 1 至 1000 之間' })
      return
    }
    set({ refLimit: parsed, refLimitError: null })
  },

  setReplyText: (text) => set({ replyText: text, replyEdited: true }),

  generate: async (mentionId) => {
    const { refLimit, refLimitError, referenceSpaceIds } = get()
    if (refLimitError || !Number.isInteger(refLimit)) return

    controller?.abort()
    controller = new AbortController()
    const signal = controller.signal

    set({
      streaming: true,
      raw: '',
      meta: null,
      draftId: null,
      error: null,
      mentionId,
      replyText: '',
      replyEdited: false,
    })

    await streamSse(
      streamUrls.draft(mentionId),
      // provider 是選填：選「自動」時整個欄位不出現，交給伺服器解析
      { reference_space_ids: referenceSpaceIds, limit: refLimit, ...providerRequestField() },
      {
        onMeta: (meta) => set({ meta }),
        onChunk: (chunk) =>
          set((state) => {
            const raw = state.raw + (chunk.text ?? '')
            // 使用者還沒動過編輯器時，讓建議回話跟著串流即時更新
            const replyText = state.replyEdited ? state.replyText : splitDraft(raw).reply
            return { raw, replyText }
          }),
        onDone: (done) =>
          set((state) => ({
            streaming: false,
            draftId: done.draft_id ?? null,
            replyText: state.replyEdited ? state.replyText : splitDraft(state.raw).reply.trim(),
          })),
        onError: (event) =>
          set({
            streaming: false,
            error: streamErrorMessage(event.code, event.message, get().meta?.provider),
          }),
      },
      signal,
    ).catch((err) => {
      set({ streaming: false, error: errorMessage(err) })
    })

    if (get().streaming) set({ streaming: false })
  },

  abort: () => {
    controller?.abort()
    controller = null
    set({ streaming: false })
  },

  reset: () => {
    controller?.abort()
    controller = null
    set({
      streaming: false,
      raw: '',
      meta: null,
      draftId: null,
      error: null,
      mentionId: null,
      replyText: '',
      replyEdited: false,
      sending: false,
    })
  },

  send: async (mentionId) => {
    const { replyText, draftId } = get()
    if (!replyText.trim()) return null
    set({ sending: true, error: null })
    try {
      const result = await api.sendReply(mentionId, { text: replyText, draft_id: draftId })
      return result.mention ?? null
    } catch (err) {
      set({ error: errorMessage(err) })
      throw err
    } finally {
      set({ sending: false })
    }
  },
}))
