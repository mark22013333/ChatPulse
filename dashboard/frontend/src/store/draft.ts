import { create } from 'zustand'
import { api, errorMessage, LIMIT_DEFAULT, streamUrls } from '@/lib/api'
import { parseCodeTerms } from '@/lib/codeTerms'
import { streamSse } from '@/lib/sse'
import { streamErrorMessage } from '@/lib/aiErrors'
import { providerRequestField } from '@/store/providers'
import type { CodeEnvironment, DraftPolishMeta, Mention, SseMeta } from '@/lib/types'

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

/** 一次草稿要查的專案與環境。同一個 project_id 配不同環境＝比對正式與 UAT。 */
export interface CodeRefSelection {
  project_id: number
  environment: CodeEnvironment
}

interface DraftState {
  /** 勾選的 Reference Space —— 規格 7.3：預設一個都不勾 */
  referenceSpaceIds: string[]
  /** 勾選的參考專案 —— 與 Reference Space 同樣預設不勾（ADR-0006） */
  codeRefs: CodeRefSelection[]
  /**
   * 手動指定的檢索關鍵字（ADR-0006 的逃生門）。存的是**輸入框那一行原文**，
   * 不是切好的陣列——切好的話輸入框就沒辦法讓人打逗號與空白了。
   * 送出前用 `parseCodeTerms()` 切。空字串＝不覆寫，讓後端自動抽詞。
   */
  codeTerms: string
  referenceSearch: string
  refLimit: number
  refLimitError: string | null

  /**
   * 回覆設定（ADR-0007）。這五個欄位與上面的參考來源勾選同一類：
   * 它們是**跨 Mention 的偏好**，所以 `reset()` 刻意不清空它們
   * （切一則 Mention 就把選好的口氣洗掉會很難用）。
   *
   * `null` 一律代表「沒有覆寫，照 Viewer 偏好或系統預設」；
   * 要表達「這一次明確不使用」則送 `NONE_ID`（0）給後端。
   */
  toneId: string | null
  personaId: number | null
  customPrompt: string
  customPromptId: number | null
  sepiaEnabled: boolean | null

  streaming: boolean
  raw: string
  meta: SseMeta | null
  draftId: number | null
  error: string | null
  /** 這份草稿屬於哪一則 Mention */
  mentionId: number | null
  /** 這次潤稿的結果（沒開潤稿時是 null）。UI 用它顯示「Sepia 有沒有生效」。 */
  polish: DraftPolishMeta | null

  /** 行內編輯器的內容 */
  replyText: string
  /** 使用者動過編輯器之後就不再被串流覆寫 */
  replyEdited: boolean

  sending: boolean

  toggleReference: (spaceId: string) => void
  clearReferences: () => void
  toggleCodeRef: (projectId: number, environment: CodeEnvironment) => void
  clearCodeRefs: () => void
  setCodeTerms: (value: string) => void
  setReferenceSearch: (value: string) => void
  setRefLimit: (raw: string) => void
  setToneId: (toneId: string | null) => void
  setPersonaId: (personaId: number | null) => void
  setCustomPrompt: (value: string) => void
  setCustomPromptId: (promptId: number | null) => void
  setSepiaEnabled: (enabled: boolean | null) => void
  setReplyText: (text: string) => void
  /** `mergeIds` 是要「一起回」的其他 Mention（不含 mentionId 自己） */
  generate: (mentionId: number, mergeIds?: number[]) => Promise<void>
  abort: () => void
  reset: () => void
  send: (mentionId: number) => Promise<Mention[]>
}

/**
 * 「這一次明確不使用」的哨兵，要與後端的 `NONE_ID` 一致。
 *
 * 需要它是因為 `null` 已經被「沿用 Viewer 偏好」佔用了：Viewer 設了預設
 * Persona 之後，「這次不要用」沒有別的方式表達——送 `null` 會被當成
 * 「照偏好來」，於是使用者關不掉它。
 */
export const NONE_ID = 0

/**
 * 送出時要結掉哪幾則：以伺服器在 meta.answering 回報的為準。
 *
 * 不用送出前的勾選，是因為那兩份可能不一致——伺服器會擋掉不合規的項目，
 * 沿用勾選會讓使用者以為某則回過了、其實沒有。meta 還沒到（草稿失敗、
 * 舊版後端）就退回只回主要那則。
 */
function answeringIds(meta: SseMeta | null, mentionId: number): number[] {
  const ids = (meta?.answering ?? []).map((a) => a.mention_id)
  return ids.filter((id) => id !== mentionId)
}

let controller: AbortController | null = null

export const useDraftStore = create<DraftState>((set, get) => ({
  referenceSpaceIds: [],
  codeRefs: [],
  codeTerms: '',
  referenceSearch: '',
  refLimit: LIMIT_DEFAULT,
  refLimitError: null,

  toneId: null,
  personaId: null,
  customPrompt: '',
  customPromptId: null,
  sepiaEnabled: null,

  streaming: false,
  raw: '',
  meta: null,
  draftId: null,
  error: null,
  mentionId: null,
  polish: null,

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

  toggleCodeRef: (projectId, environment) =>
    set((state) => {
      const exists = state.codeRefs.some(
        (r) => r.project_id === projectId && r.environment === environment,
      )
      return {
        codeRefs: exists
          ? state.codeRefs.filter(
              (r) => !(r.project_id === projectId && r.environment === environment),
            )
          : [...state.codeRefs, { project_id: projectId, environment }],
      }
    }),

  clearCodeRefs: () => set({ codeRefs: [] }),
  setCodeTerms: (codeTerms) => set({ codeTerms }),
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

  setToneId: (toneId) => set({ toneId }),
  setPersonaId: (personaId) => set({ personaId }),
  setCustomPrompt: (value) => set({ customPrompt: value }),
  setCustomPromptId: (customPromptId) => set({ customPromptId }),
  setSepiaEnabled: (sepiaEnabled) => set({ sepiaEnabled }),

  setReplyText: (text) => set({ replyText: text, replyEdited: true }),

  generate: async (mentionId, mergeIds = []) => {
    const {
      refLimit,
      refLimitError,
      referenceSpaceIds,
      codeRefs,
      codeTerms,
      toneId,
      personaId,
      customPrompt,
      customPromptId,
      sepiaEnabled,
    } = get()
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
      polish: null,
      replyText: '',
      replyEdited: false,
    })

    // 回覆設定一律「有值才送」：省略欄位代表「沿用 Viewer 偏好」，
    // 送 null 在後端是「清除偏好」的意思（只用在 PATCH /preferences），
    // 兩者不可混用——見 lib/api.ts 的 updatePreferences 註解。
    const replyFields: Record<string, unknown> = {}
    if (toneId !== null) replyFields.tone_id = toneId
    if (personaId !== null) replyFields.persona_id = personaId
    if (customPrompt.trim()) replyFields.custom_prompt = customPrompt.trim()
    else if (customPromptId !== null) replyFields.custom_prompt_id = customPromptId
    if (sepiaEnabled !== null) replyFields.sepia_enabled = sepiaEnabled

    // `code_terms` 只在真的有填時才送。空陣列與省略在後端是同一件事
    // （`req.code_terms ... or extract_search_terms(...)`），送空的只是噪音。
    // 送出去就是**完全取代**自動抽詞，不是附加。
    const terms = parseCodeTerms(codeTerms)
    if (terms.length) replyFields.code_terms = terms

    await streamSse(
      streamUrls.draft(mentionId),
      // provider 是選填：選「自動」時整個欄位不出現，交給伺服器解析
      {
        reference_space_ids: referenceSpaceIds,
        limit: refLimit,
        merge_mention_ids: mergeIds.filter((id) => id !== mentionId),
        code_refs: codeRefs,
        ...providerRequestField(),
        ...replyFields,
      },
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
            polish: done.polish ?? null,
            // `done.reply` 是潤稿後的版本。**必須採用它**，否則開了 Sepia
            // 之後畫面上是未潤稿的內容、資料庫是潤稿後的內容，而使用者
            // 按送出時送的是畫面這一份——那等於 Sepia 完全沒生效，
            // 而且從畫面看不出來。
            //
            // 使用者已經動過編輯器時仍然尊重他的版本：他的編輯比潤稿更晚、
            // 也更明確。這與 onChunk 的 replyEdited 判斷是同一條規則。
            replyText: state.replyEdited
              ? state.replyText
              : (done.reply ?? splitDraft(state.raw).reply).trim(),
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
    // 刻意**不清** referenceSpaceIds／codeRefs／codeTerms／refLimit／referenceSearch
    // 與回覆設定（toneId／personaId／customPrompt／customPromptId／sepiaEnabled）：
    // 那些是跨 Mention 的偏好，切一則就洗掉會很難用。
    // `polish` 相反——它是這一次草稿的結果，要跟著清。
    set({
      streaming: false,
      raw: '',
      meta: null,
      draftId: null,
      error: null,
      mentionId: null,
      polish: null,
      replyText: '',
      replyEdited: false,
      sending: false,
    })
  },

  send: async (mentionId) => {
    const { replyText, draftId, meta } = get()
    if (!replyText.trim()) return []
    set({ sending: true, error: null })
    try {
      const result = await api.sendReply(mentionId, {
        text: replyText,
        draft_id: draftId,
        merge_mention_ids: answeringIds(meta, mentionId),
      })
      // 新版後端回 mentions（合併回覆時 > 1 則）；舊版只有 mention
      if (result.mentions?.length) return result.mentions
      return result.mention ? [result.mention] : []
    } catch (err) {
      set({ error: errorMessage(err) })
      throw err
    } finally {
      set({ sending: false })
    }
  },
}))
