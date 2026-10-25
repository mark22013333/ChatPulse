import { create } from 'zustand'
import { api, ApiError, errorMessage, LIMIT_DEFAULT, streamUrls } from '@/lib/api'
import { useAuthStore } from '@/store/auth'
import { parseCodeTerms } from '@/lib/codeTerms'
import { streamSse } from '@/lib/sse'
import { streamErrorMessage } from '@/lib/aiErrors'
import { modelRequestField, providerRequestField } from '@/store/providers'
import type { CodeEnvironment, DraftPolishMeta, Mention, SseMeta, ProjectChoice, ProjectProfile, ReplyAssessment, ReviewProgress } from '@/lib/types'

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

/**
 * 「建議回話」的標題已經串流出來了嗎。
 *
 * 與 `splitDraft()` 的差別是**只做一次 regex test、不切字串**。給那些每個
 * chunk 都會被求值的地方用（app 級的串流宣告 selector），那裡不需要內容、
 * 只需要這個布林。REPLY_HEADING 沒有 `g` 旗標，所以 `.test()` 不會推進
 * lastIndex，可以安全重複呼叫。
 */
export function hasReplyHeading(raw: string): boolean {
  return REPLY_HEADING.test(raw)
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

export interface ActiveDraftScope {
  mentionId: number
  projectChoice?: ProjectChoice | null
  epoch: symbol
  referenceSpaceIds: string[]
  codeRefs: CodeRefSelection[]
  codeTerms: string
  referenceSearch: string
  operation: { kind: 'restore' | 'generate'; id: symbol } | null
}

export interface SendReceipt {
  mentions: Mention[]
  scopeEpoch: symbol
}

interface DraftState {
  active: ActiveDraftScope | null
  sendingMentionIds: number[]
  refLimit: number
  refLimitError: string | null

  /**
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
  projectStatus: 'complete' | 'partial' | 'stale' | null
  assessment: ReplyAssessment | null
  receiptId: string | null
  reviewRevision: number | null
  reviewPending: boolean
  reviewProgress: ReviewProgress['stage'] | null
  draftId: number | null
  error: string | null
  /** 這次潤稿的結果（沒開潤稿時是 null）。UI 用它顯示「Sepia 有沒有生效」。 */
  polish: DraftPolishMeta | null
  /**
   * 目前這份草稿是從資料庫**還原**的，不是這次串流產生的。
   *
   * 證據欄要靠它把「這項證據沒有保存」與「伺服器沒回報」分開講
   * （見 `lib/evidence.ts` 的 `restored`）。按「重新產生」會回到 false。
   */
  restored: boolean
  /** 還原的那份草稿是什麼時候產生的（ISO 字串）。 */
  restoredAt: string | null

  /** 行內編輯器的內容 */
  replyText: string
  /** 使用者動過編輯器之後就不再被串流覆寫 */
  replyEdited: boolean

  activateMention: (mentionId: number) => void
  selectProject: (profile: ProjectProfile | null) => void
  toggleProjectSource: (bindingId: string) => void
  setProjectSourceUse: (bindingId: string, use: 'metadata_only' | 'analysis_only' | 'reply_context') => void
  setProjectEnvironment: (environment: CodeEnvironment | null) => void
  setProjectConsent: (consent: ProjectChoice['consent']) => void

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
  review: (mentionId: number) => Promise<void>
  /** `mergeIds` 是要「一起回」的其他 Mention（不含 mentionId 自己） */
  generate: (mentionId: number, mergeIds?: number[]) => Promise<void>
  /**
   * 把這一則**已經存下來**的草稿讀回來（`GET /mentions/{id}/draft`）。
   *
   * 回 true 代表真的還原了一份。沒有草稿（DRAFT_NOT_FOUND）回 false 並且**不設 error**
   * ——多數 Mention 本來就沒產過草稿，那是正常狀態不是失敗。
   */
  loadStored: (mentionId: number) => Promise<boolean>
  abort: () => void
  reset: () => void
  send: (mentionId: number) => Promise<SendReceipt>
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

const EMPTY_SPACES: string[] = []
const EMPTY_CODE_REFS: CodeRefSelection[] = []
export const selectReferenceSpaceIds = (state: DraftState) => state.active?.referenceSpaceIds ?? EMPTY_SPACES
export const selectCodeRefs = (state: DraftState) => state.active?.codeRefs ?? EMPTY_CODE_REFS
export const selectSending = (state: DraftState) => state.active !== null && state.sendingMentionIds.includes(state.active.mentionId)

const EMPTY_OUTPUT = {
  streaming: false, raw: '', meta: null, draftId: null, error: null, projectStatus: null,
  polish: null, restored: false, restoredAt: null, replyText: '', replyEdited: false,
  assessment: null, receiptId: null, reviewRevision: null, reviewPending: false, reviewProgress: null,
}

type ContentLease = { epoch: symbol; operationId: symbol }
let controller: AbortController | null = null
let reviewRequest: symbol | null = null

export const useDraftStore = create<DraftState>((set, get) => {
  const commit = (lease: ContentLease, update: (state: DraftState) => Partial<DraftState>, finish = false) => {
    let accepted = false
    set((state) => {
      const active = state.active
      if (!active || active.epoch !== lease.epoch || active.operation?.id !== lease.operationId) return state
      accepted = true
      return { ...update(state), ...(finish ? { active: { ...active, operation: null } } : {}) }
    })
    return accepted
  }
  const begin = (kind: 'restore' | 'generate'): ContentLease => {
    const active = get().active
    if (!active) throw new Error('請先選擇 Mention')
    const id = Symbol(kind)
    set({ active: { ...active, operation: { kind, id } } })
    return { epoch: active.epoch, operationId: id }
  }
  const updateSources = (update: (scope: ActiveDraftScope) => Partial<ActiveDraftScope>, forceInvalidate = false) => {
    const state = get()
    if (!forceInvalidate && !state.active?.projectChoice && !state.meta?.project_scope) {
      set((current) => current.active ? { active: { ...current.active, ...update(current.active) } } : current)
      return
    }
    const oldController = controller
    controller = null
    reviewRequest = null
    set((state) => state.active ? {
      ...EMPTY_OUTPUT,
      active: { ...state.active, ...update(state.active), epoch: Symbol('selection'), operation: null },
    } : state)
    oldController?.abort()
  }
  const invalidateProject = () => {
    if (get().active?.projectChoice || get().meta?.project_scope) updateSources(() => ({}))
  }

  const activate = (mentionId: number) => {
    if (get().active?.mentionId === mentionId) return false
    const oldController = controller
    controller = null
    reviewRequest = null
    set({ ...EMPTY_OUTPUT, active: {
      mentionId, epoch: Symbol('mention'), referenceSpaceIds: [], codeRefs: [],
      codeTerms: '', referenceSearch: '', operation: null,
    } })
    oldController?.abort()
    return true
  }

  return {
    active: null,
    sendingMentionIds: [],
    refLimit: LIMIT_DEFAULT,
    refLimitError: null,
    toneId: null,
    personaId: null,
    customPrompt: '',
    customPromptId: null,
    sepiaEnabled: null,
    ...EMPTY_OUTPUT,

    activateMention: (mentionId) => {
      if (activate(mentionId)) void get().loadStored(mentionId)
    },

    selectProject: (profile) => updateSources(() => ({
      projectChoice: profile ? { profile, sourceUses: {}, environment: null, consent: null } : null,
      referenceSpaceIds: [], codeRefs: [], codeTerms: '',
    }), true),
    toggleProjectSource: (bindingId) => updateSources((scope) => ({
      projectChoice: scope.projectChoice ? {
        ...scope.projectChoice, consent: null,
        sourceUses: { ...scope.projectChoice.sourceUses, [bindingId]: scope.projectChoice.sourceUses[bindingId] === 'reply_context' ? 'metadata_only' : 'reply_context' },
      } : null,
    })),
    setProjectSourceUse: (bindingId, use) => updateSources((scope) => ({
      projectChoice: scope.projectChoice ? { ...scope.projectChoice, consent: null,
        sourceUses: { ...scope.projectChoice.sourceUses, [bindingId]: use },
      } : null,
    })),
    setProjectEnvironment: (environment) => updateSources((scope) => ({
      projectChoice: scope.projectChoice ? { ...scope.projectChoice, environment, consent: null } : null,
    })),
    setProjectConsent: (consent) => updateSources((scope) => ({
      projectChoice: scope.projectChoice ? { ...scope.projectChoice, consent } : null,
    })),
    toggleReference: (spaceId) => updateSources((scope) => ({
      referenceSpaceIds: scope.referenceSpaceIds.includes(spaceId)
        ? scope.referenceSpaceIds.filter((id) => id !== spaceId)
        : [...scope.referenceSpaceIds, spaceId],
    })),
    clearReferences: () => updateSources(() => ({ referenceSpaceIds: [] })),
    toggleCodeRef: (projectId, environment) => updateSources((scope) => ({
      codeRefs: scope.codeRefs.some((ref) => ref.project_id === projectId && ref.environment === environment)
        ? scope.codeRefs.filter((ref) => !(ref.project_id === projectId && ref.environment === environment))
        : [...scope.codeRefs, { project_id: projectId, environment }],
    })),
    clearCodeRefs: () => updateSources(() => ({ codeRefs: [] })),
    setCodeTerms: (codeTerms) => updateSources(() => ({ codeTerms })),
    setReferenceSearch: (referenceSearch) => set((state) => state.active ? { active: { ...state.active, referenceSearch } } : state),

    setRefLimit: (raw) => {
      invalidateProject()
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

    setToneId: (toneId) => { invalidateProject(); set({ toneId }) },
    setPersonaId: (personaId) => { invalidateProject(); set({ personaId }) },
    setCustomPrompt: (value) => { invalidateProject(); set({ customPrompt: value }) },
    setCustomPromptId: (customPromptId) => { invalidateProject(); set({ customPromptId }) },
    setSepiaEnabled: (sepiaEnabled) => { invalidateProject(); set({ sepiaEnabled }) },

    setReplyText: (text) => { reviewRequest = null; set({ replyText: text, replyEdited: true, receiptId: null, reviewPending: false }) },

    review: async (mentionId) => {
      const state = get()
      const active = state.active
      const digest = state.meta?.project_scope?.digest
      if (!active || active.mentionId !== mentionId || !digest || !state.draftId || state.projectStatus !== 'complete' || !state.replyText.trim()) {
        throw new Error('請先產生完整的專案草稿')
      }
      const epoch = active.epoch
      const text = state.replyText
      const requestId = Symbol('review')
      reviewRequest = requestId
      set({ reviewPending: true, receiptId: null, error: null })
      try {
        const result = await api.reviewDraft(mentionId, { draft_id: state.draftId, text, scope_digest: digest })
        set((current) => current.active?.epoch === epoch && reviewRequest === requestId && current.replyText === text
          && current.draftId === result.draft_id && current.meta?.project_scope?.digest === digest
          ? { assessment: result.assessment, receiptId: result.assessment.status === 'evaluated' ? result.receipt_id : null,
              reviewRevision: result.review_revision, reviewPending: false } : current)
      } catch (err) {
        set((current) => current.active?.epoch === epoch && reviewRequest === requestId
          ? { error: errorMessage(err), reviewPending: false } : current)
      } finally { if (reviewRequest === requestId) reviewRequest = null }
    },

    generate: async (mentionId, mergeIds = []) => {
      get().activateMention(mentionId)
      const state = get()
      const { refLimit, refLimitError, toneId, personaId, customPrompt, customPromptId, sepiaEnabled } = state
      if (refLimitError || !Number.isInteger(refLimit) || !state.active) return
      const choice = state.active.projectChoice
      const evidenceMode = Boolean(choice && useAuthStore.getState().me?.features?.reply_evidence)
      if (choice && (!useAuthStore.getState().me?.features?.project_context
        || !useAuthStore.getState().me?.preferences.project_context_opt_in)) {
        set({ error: '請先啟用專案脈絡' })
        return
      }
      if (choice && (!choice.consent || mergeIds.some((id) => id !== mentionId))) {
        set({ error: '請確認確切目標；專案範圍只支援單一 Mention' })
        return
      }
      const referenceSpaceIds = [...state.active.referenceSpaceIds]
      const codeRefs = state.active.codeRefs.map((ref) => ({ ...ref }))
      const codeTerms = state.active.codeTerms
      const lease = begin('generate')
      controller?.abort()
      const runController = new AbortController()
      controller = runController
      const signal = runController.signal
      commit(lease, () => ({ ...EMPTY_OUTPUT, streaming: true, projectStatus: choice ? 'partial' : null }))

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
        // provider 與 model 都是選填：選「自動」／「跟隨預設」時整個欄位不出現，
        // 交給伺服器解析。model 帶的是裸值，不是用量欄位那種帶前綴的識別字。
        {
          ...(choice ? {
            project_selection: {
              profile_id: choice.profile.id, revision: choice.profile.revision,
              sources: choice.profile.sources.map((source) => ({
                binding_id: source.id,
                use: evidenceMode ? (choice.sourceUses[source.id] ?? 'metadata_only')
                  : choice.sourceUses[source.id] === 'reply_context' ? 'reply_context' : 'metadata_only',
              })),
              ...(evidenceMode ? { environment: choice.environment ?? null } : {}),
              target_space_id: choice.consent?.spaceId,
              target_thread_name: choice.consent?.threadName,
            },
          } : {
            reference_space_ids: referenceSpaceIds,
            merge_mention_ids: mergeIds.filter((id) => id !== mentionId),
            code_refs: codeRefs,
          }),
          limit: refLimit,
          ...providerRequestField(),
          ...modelRequestField(),
          ...replyFields,
        },
        {
          onMeta: (meta) => { commit(lease, () => ({ meta })) },
          onReviewProgress: (progress) => {
            if (progress.mention_id === mentionId) commit(lease, (current) =>
              current.meta?.project_scope?.digest === progress.scope_digest ? { reviewProgress: progress.stage } : {})
          },
          onChunk: (chunk) =>
            commit(lease, (state) => {
              if (evidenceMode) return {}
              const raw = state.raw + (chunk.text ?? '')
              // 使用者還沒動過編輯器時，讓建議回話跟著串流即時更新
              const replyText = state.replyEdited ? state.replyText : splitDraft(raw).reply
              return { raw, replyText }
            }),
          onDone: (done) =>
            commit(lease, (state) => ({
              raw: evidenceMode ? (done.reply ? `### ✍️ 建議回話\n${done.reply}` : '') : state.raw,
              streaming: false,
              draftId: done.draft_id ?? null,
              projectStatus: choice
                ? (done.project_status === 'complete' && done.scope_digest === state.meta?.project_scope?.digest && done.draft_id ? 'complete' : 'partial')
                : null,
              polish: done.polish ?? null,
              assessment: evidenceMode ? done.assessment ?? null : null,
              receiptId: evidenceMode && !state.replyEdited && done.assessment?.status === 'evaluated' ? done.receipt_id ?? null : null,
              reviewRevision: evidenceMode ? done.review_revision ?? null : null,
              reviewProgress: null,
              // `done.reply` 是潤稿後的版本。**必須採用它**，否則開了 Sepia
              // 之後畫面上是未潤稿的內容、資料庫是潤稿後的內容，而使用者
              // 按送出時送的是畫面這一份——那等於 Sepia 完全沒生效，
              // 而且從畫面看不出來。
              //
              // 使用者已經動過編輯器時仍然尊重他的版本：他的編輯比潤稿更晚、
              // 也更明確。這與 onChunk 的 replyEdited 判斷是同一條規則。
              replyText: state.replyEdited
                ? state.replyText
                : evidenceMode ? done.reply ?? '' : (done.reply ?? splitDraft(state.raw).reply).trim(),
            }), true),
          onError: (event) =>
            commit(lease, (state) => ({
              streaming: false,
              error: streamErrorMessage(event.code, event.message, state.meta?.provider),
            }), true),
        },
        signal,
      ).catch((err) => {
        commit(lease, () => ({ streaming: false, error: errorMessage(err) }), true)
      })

      commit(lease, () => ({ streaming: false }), true)
      if (controller === runController) controller = null
    },

    abort: () => {
      const oldController = controller
      controller = null
      set((state) => ({ streaming: false, active: state.active ? { ...state.active, operation: null } : null }))
      oldController?.abort()
    },

    reset: () => {
      const oldController = controller
      controller = null
      set({ ...EMPTY_OUTPUT, active: null })
      oldController?.abort()
    },

    loadStored: async (mentionId) => {
      activate(mentionId)
      const state = get()
      if (state.streaming || state.raw) return false
      const lease = begin('restore')
      try {
        const stored = await api.storedDraft(mentionId)
        const cfg = stored.generation_config ?? {}
        // 2026-09-11 之後產生的草稿把**整份 meta** 存了下來（就是當初送給
        // 瀏覽器的那一份），證據欄可以完整還原。有就直接用，不要自己重拼——
        // 重拼等於再寫一份會跟後端漂移的邏輯。
        //
        // 更早的那批（本機 53 筆）只有平鋪的欄位，就拼一份**局部** meta。
        // 刻意不填 context／reference_spaces／answering／image_count：那些
        // 從來沒存過，填假的比留空危險得多，而 `toEvidence` 對缺的欄位本來
        // 就會畫成 missing（配合 restored 旗標說出正確的理由）。
        const meta = (cfg.meta ?? {
          type: 'meta',
          mention_id: mentionId,
          provider: cfg.provider,
          model: cfg.model,
          reply: {
            tone: cfg.tone,
            tone_label: cfg.tone_label,
            persona_id: cfg.persona_id,
            persona_name: cfg.persona_name,
            custom_prompt: cfg.custom_prompt,
            custom_prompt_id: cfg.custom_prompt_id,
            sepia: cfg.sepia === true,
          },
        }) satisfies SseMeta
        const polish: DraftPolishMeta | null =
          cfg.polished === undefined
            ? null
            : ({
                polished: cfg.polished,
                polisher: cfg.polisher ?? 'sepia',
                polish_model: cfg.polish_model,
                fallback_reason: cfg.fallback_reason,
              } satisfies DraftPolishMeta)

        const me = useAuthStore.getState().me
        const projectAvailable = me?.features?.project_context && me.preferences.project_context_opt_in
        return commit(lease, () => ({
          streaming: false,
          raw: stored.content_md,
          meta,
          polish,
          draftId: stored.draft_id,
          assessment: me?.features?.reply_evidence && meta.project_scope ? cfg.assessment ?? null : null,
          receiptId: me?.features?.reply_evidence && stored.scope_state === 'current' && cfg.assessment?.status === 'evaluated'
            ? cfg.receipt_id ?? null : null,
          reviewRevision: me?.features?.reply_evidence && meta.project_scope ? cfg.review_revision ?? null : null,
          projectStatus: meta.project_scope
            ? (!projectAvailable || stored.scope_state !== 'current' ? 'stale' : cfg.project_status === 'complete' ? 'complete' : 'partial')
            : null,
          error: null,
          restored: true,
          restoredAt: stored.created_at,
          replyText: stored.content_md,
          replyEdited: false,
        }), true)
      } catch (err) {
        commit(lease, () => err instanceof ApiError && err.code === 'DRAFT_NOT_FOUND'
          ? {} : { error: errorMessage(err) }, true)
        return false
      }
    },

    send: async (mentionId) => {
      const { active, replyText, draftId, meta, sendingMentionIds, projectStatus, receiptId, reviewRevision, assessment, reviewPending } = get()
      if (!active || active.mentionId !== mentionId) throw new Error('草稿與 Mention 不一致，請重新確認')
      if (sendingMentionIds.includes(mentionId)) throw new Error('這則 Mention 正在送出')
      if (!replyText.trim()) throw new Error('回話內容不可空白')
      if (projectStatus !== null && (projectStatus !== 'complete' || !meta?.project_scope || !draftId)) {
        throw new Error('專案草稿尚未完整產生或已失效，請重新產生')
      }
      if (meta?.project_scope && projectStatus !== 'complete') throw new Error('專案草稿無法送出')
      const evidenceMode = Boolean(meta?.project_scope && useAuthStore.getState().me?.features?.reply_evidence)
      if (evidenceMode && (reviewPending || !receiptId || reviewRevision === null || assessment?.status !== 'evaluated')) {
        throw new Error('請先查核目前的回話，再確認送出')
      }
      const scopeEpoch = active.epoch
      const body = { text: replyText, draft_id: draftId, merge_mention_ids: answeringIds(meta, mentionId),
        ...(meta?.project_scope ? { scope_digest: meta.project_scope.digest } : {}),
        ...(evidenceMode ? { receipt_id: receiptId!, review_revision: reviewRevision! } : {}), }
      set({ sendingMentionIds: [...sendingMentionIds, mentionId], error: null })
      try {
        const result = await api.sendReply(mentionId, body)
        const mentions = result.mentions?.length ? result.mentions : result.mention ? [result.mention] : []
        return { mentions, scopeEpoch }
      } catch (err) {
        set((state) => state.active?.epoch === scopeEpoch ? { error: errorMessage(err) } : state)
        throw err
      } finally {
        set((state) => ({ sendingMentionIds: state.sendingMentionIds.filter((id) => id !== mentionId) }))
      }
    },
  }
})
