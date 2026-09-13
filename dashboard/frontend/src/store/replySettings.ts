import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import { useDraftStore } from '@/store/draft'
import type {
  Persona,
  PersonaSourceInfo,
  PolisherInfo,
  Preferences,
  ReplyPrompt,
  ReplyTone,
  SepiaRulesInfo,
} from '@/lib/types'

/**
 * 回覆設定的目錄（ADR-0007）：口氣清單、Persona、提示詞 preset、潤稿器可用性。
 *
 * 為什麼與 `store/draft.ts` 分開：draft store 存的是「這一次草稿要用什麼」
 * （per-draft override），這裡存的是「有哪些可以選」（catalog）。前者跟著
 * 草稿生命週期，後者是整個 session 共用、只需要載入一次。混在一起會讓
 * `reset()` 很難寫——清掉選擇是對的，清掉目錄則會讓下拉選單變空。
 *
 * `providers.ts` 的 `initialised` 旗標在這裡也照抄：偏好有兩個入口
 * （`/me` 與明確的 preferences 更新），只在第一次決定選擇，
 * 之後不覆寫使用者當下選的。
 */
interface ReplySettingsState {
  tones: ReplyTone[]
  /** 伺服器預設的口氣 id（沒有任何偏好時實際會用的那個） */
  serverDefaultTone: string
  personas: Persona[]
  sources: PersonaSourceInfo[]
  replyPrompts: ReplyPrompt[]
  polishers: PolisherInfo[]
  sepiaRules: SepiaRulesInfo

  loading: boolean
  /** 目錄是否已經載入過（避免每次切頁籤重打 4 個端點） */
  loaded: boolean
  /** 匯入／更新 Persona 進行中 */
  busy: boolean
  error: string | null
  /** 偏好是否已經套用過（避免覆寫使用者當下的選擇） */
  initialised: boolean

  /** 載入目錄。已載入過就直接返回；`force: true` 強制重載。 */
  load: (options?: { force?: boolean }) => Promise<void>
  loadPersonas: () => Promise<void>
  loadReplyPrompts: () => Promise<void>
  applyPreferences: (prefs: Preferences | null | undefined) => void
  importPersona: (body: {
    source_type: string
    repository?: string
    persona?: string
    url?: string
    ref?: string
    name?: string
    /** 回傳整個結果而不只是 persona，因為 `notice`（例如「這是從 repo
     *  根目錄匯入的」）與 persona 同等重要——它是要**看一眼**的提醒。 */
  }) => Promise<{ persona: Persona; notice?: string | null } | null>
  createPersona: (body: {
    name: string
    description?: string
    raw_text?: string
    profile?: Record<string, unknown>
  }) => Promise<Persona | null>
  refreshPersona: (id: number) => Promise<{ persona: Persona; changed: boolean } | null>
  updatePersona: (
    id: number,
    body: { name?: string; description?: string; enabled?: boolean },
  ) => Promise<Persona | null>
  deletePersona: (id: number) => Promise<boolean>
  createReplyPrompt: (body: {
    name: string
    description?: string
    prompt: string
  }) => Promise<ReplyPrompt | null>
  updateReplyPrompt: (
    id: number,
    body: { name?: string; description?: string; prompt?: string },
  ) => Promise<ReplyPrompt | null>
  deleteReplyPrompt: (id: number) => Promise<boolean>
  saveDefaults: () => Promise<boolean>
  clearError: () => void
}

/** Select 用來表達「沿用 Viewer 偏好／伺服器預設」的哨兵值。 */
export const INHERIT = '__inherit__'
/** Select 用來表達「這一次明確不使用」的哨兵值。 */
export const NONE = '__none__'

/** 公開人物 Persona 的免責提示。自訂 Persona 不需要顯示。 */
export const PUBLIC_FIGURE_NOTICE = '基於公開資料提煉的風格／思考模型，不代表本人。'

/** Sepia 潤稿器現在可不可用；不可用時的原因要說得出下一步。 */
export function sepiaAvailability(polishers: PolisherInfo[]): {
  available: boolean
  reason: string
} {
  const sepia = polishers.find((p) => p.name === 'sepia')
  if (!sepia) return { available: false, reason: '這個版本的伺服器沒有 Sepia 潤稿' }
  return { available: sepia.available, reason: sepia.reason }
}

/** tone id → 顯示標籤；認不出來就回原字串（不要在顯示路徑上壞掉）。 */
export function toneLabel(tones: ReplyTone[], id: string | null): string {
  if (!id) return ''
  return tones.find((t) => t.id === id)?.label ?? id
}

/**
 * 側欄收合狀態下要顯示的設定摘要。
 *
 * 摘要存在的理由是**收合不能讓設定變隱形**：使用者上次選了「工程師協作 ＋
 * Persona ＋ Sepia」，收合之後如果只看到「回覆設定」四個字，他會以為什麼都
 * 沒選而重新設一次，或更糟——以為沒生效。
 *
 * 放在 store 而不是元件裡是因為 vitest 跑在 node 環境、只收 `.test.ts`，
 * 元件測不到；判斷邏輯留在 JSX 裡就等於沒有測試覆蓋。
 */
export function settingsSummary(args: {
  tones: ReplyTone[]
  toneId: string | null
  personas: Persona[]
  personaId: number | null
  customPrompt: string
  customPromptId: number | null
  sepiaEnabled: boolean | null
}): string {
  const parts: string[] = []
  if (args.toneId) parts.push(toneLabel(args.tones, args.toneId))
  if (args.personaId) {
    const persona = args.personas.find((p) => p.id === args.personaId)
    if (persona) parts.push(persona.name)
  }
  if (args.customPrompt.trim()) parts.push('自訂提示')
  else if (args.customPromptId) parts.push('提示詞')
  if (args.sepiaEnabled) parts.push('Sepia')
  return parts.length ? parts.join(' · ') : '未設定'
}

/** Persona 的來源說明（顯示在選單與詳細資訊裡）。 */
export function personaSourceLine(persona: Persona): string {
  if (persona.source_type === 'manual') return '自訂 Persona'
  const repo = persona.source_repository ?? persona.source_url ?? '外部來源'
  const sha = persona.source_commit_sha ? persona.source_commit_sha.slice(0, 7) : '未記錄'
  return `${repo} @ ${sha}`
}

/** 只有公開人物來源才需要顯示免責提示；自訂 Persona 不需要。 */
export function needsPublicFigureNotice(persona: Persona | undefined): boolean {
  return Boolean(persona && persona.source_type !== 'manual')
}

/**
 * 把 Select 的值轉成 id 型 store 值（Persona／提示詞 preset 共用）。
 *
 * 回 `undefined` 代表**不要動**——這是這個函式存在的唯一理由：
 * Base UI 的 Select 在清除選擇時會給 `null`（既有慣例見 SummaryWorkspace
 * 的摘要風格下拉），而 `Number(null)` 正好是 `0`，也就是 `NONE_ID`
 * ＝「這一次明確不使用」。那會**覆寫掉 Viewer 的偏好**，而使用者
 * 根本沒做任何選擇——他設的預設 Persona 會無聲失效。
 *
 * 抽成純函式是為了讓這個陷阱有測試覆蓋：vitest 跑在 node 環境、
 * 只收 `.test.ts`，寫在 JSX 的 onValueChange 裡就沒有人能測它。
 */
export function selectToId(
  value: string | null | undefined,
  noneId: number,
): number | null | undefined {
  if (!value) return undefined
  if (value === INHERIT) return null
  if (value === NONE) return noneId
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined
}

/** 把 Select 的值轉成 tone store 值。回 `undefined` 代表不要動。 */
export function selectToTone(
  value: string | null | undefined,
): string | null | undefined {
  if (!value) return undefined
  return value === INHERIT ? null : value
}

export const useReplySettingsStore = create<ReplySettingsState>((set, get) => ({
  tones: [],
  serverDefaultTone: 'natural',
  personas: [],
  sources: [],
  replyPrompts: [],
  polishers: [],
  sepiaRules: {},

  loading: false,
  loaded: false,
  busy: false,
  error: null,
  initialised: false,

  load: async ({ force = false } = {}) => {
    // App.tsx 用條件渲染切換工作區，所以 DraftReplyWorkspace（連帶
    // ReplySettings）**每次切頁籤都會卸載重掛**。沒有這個旗標的話，
    // 每次切回草稿頁都會重打這四個端點——其中 /personas 與 /reply-prompts
    // 還會查資料庫。`loading` 只防併發，防不了重複。
    if (get().loading || (get().loaded && !force)) return
    set({ loading: true })
    try {
      // 四個端點互不相依，一起發。任何一個失敗都不該讓其他三個的結果消失，
      // 所以用 allSettled 而不是 all——拿不到 Persona 清單時，
      // 口氣下拉仍然應該可以用。
      const [tones, personas, prompts, polishers] = await Promise.allSettled([
        api.replyTones(),
        api.personas(),
        api.replyPrompts(),
        api.polishers(),
      ])
      const patch: Partial<ReplySettingsState> = {}
      if (tones.status === 'fulfilled') {
        patch.tones = tones.value.tones
        patch.serverDefaultTone = tones.value.default
      }
      if (personas.status === 'fulfilled') {
        patch.personas = personas.value.personas
        patch.sources = personas.value.sources
      }
      if (prompts.status === 'fulfilled') patch.replyPrompts = prompts.value.reply_prompts
      if (polishers.status === 'fulfilled') {
        patch.polishers = polishers.value.polishers
        patch.sepiaRules = polishers.value.sepia
      }
      set(patch)
    } finally {
      // 標成已載入即使部分端點失敗：那些失敗會讓對應的清單留空，
      // 而使用者的動作（匯入 Persona、存提示詞）各自會刷新自己那份，
      // 不需要靠重打整組來補。想強制重載的路徑用 `force: true`。
      set({ loading: false, loaded: true })
    }
  },

  loadPersonas: async () => {
    try {
      const data = await api.personas()
      set({ personas: data.personas, sources: data.sources })
    } catch (err) {
      set({ error: errorMessage(err) })
    }
  },

  loadReplyPrompts: async () => {
    try {
      const data = await api.replyPrompts()
      set({ replyPrompts: data.reply_prompts })
    } catch (err) {
      set({ error: errorMessage(err) })
    }
  },

  /**
   * 把 Viewer 的偏好套成 draft store 的初始選擇。
   *
   * 這是「重新整理後偏好還在」的實作點。技術上不套也能運作（送出時省略
   * 欄位，後端自己會讀偏好），但那樣 UI 的下拉會顯示「不使用」而實際上
   * 有生效——畫面與行為不一致比沒有預設更糟。
   *
   * `initialised` 保護：`/me` 可能在使用者已經改過選擇之後才回來。
   */
  applyPreferences: (prefs) => {
    if (!prefs || get().initialised) return
    const draft = useDraftStore.getState()
    if (prefs.default_reply_tone) draft.setToneId(prefs.default_reply_tone)
    if (prefs.default_persona_id) draft.setPersonaId(prefs.default_persona_id)
    if (prefs.default_reply_prompt_id) draft.setCustomPromptId(prefs.default_reply_prompt_id)
    if (prefs.default_sepia_enabled != null) draft.setSepiaEnabled(prefs.default_sepia_enabled)
    set({ initialised: true })
  },

  importPersona: async (body) => {
    set({ busy: true, error: null })
    try {
      const result = await api.importPersona(body)
      await get().loadPersonas()
      return { persona: result.persona, notice: result.notice ?? null }
    } catch (err) {
      set({ error: errorMessage(err) })
      return null
    } finally {
      set({ busy: false })
    }
  },

  createPersona: async (body) => {
    set({ busy: true, error: null })
    try {
      const result = await api.createPersona(body)
      await get().loadPersonas()
      return result.persona
    } catch (err) {
      set({ error: errorMessage(err) })
      return null
    } finally {
      set({ busy: false })
    }
  },

  refreshPersona: async (id) => {
    set({ busy: true, error: null })
    try {
      const result = await api.refreshPersona(id)
      await get().loadPersonas()
      return { persona: result.persona, changed: result.changed }
    } catch (err) {
      set({ error: errorMessage(err) })
      return null
    } finally {
      set({ busy: false })
    }
  },

  updatePersona: async (id, body) => {
    set({ error: null })
    try {
      const persona = await api.updatePersona(id, body)
      set((state) => ({
        personas: state.personas.map((p) => (p.id === id ? persona : p)),
      }))
      return persona
    } catch (err) {
      set({ error: errorMessage(err) })
      return null
    }
  },

  deletePersona: async (id) => {
    set({ error: null })
    try {
      await api.deletePersona(id)
      set((state) => ({ personas: state.personas.filter((p) => p.id !== id) }))
      // 後端刪除 Persona 時會順手清掉指向它的偏好；本地的當次選擇也要清，
      // 否則下拉會停在一個已經不存在的 id 上。
      if (useDraftStore.getState().personaId === id) {
        useDraftStore.getState().setPersonaId(null)
      }
      return true
    } catch (err) {
      set({ error: errorMessage(err) })
      return false
    }
  },

  createReplyPrompt: async (body) => {
    set({ error: null })
    try {
      const prompt = await api.createReplyPrompt(body)
      set((state) => ({ replyPrompts: [...state.replyPrompts, prompt] }))
      return prompt
    } catch (err) {
      set({ error: errorMessage(err) })
      return null
    }
  },

  updateReplyPrompt: async (id, body) => {
    set({ error: null })
    try {
      const prompt = await api.updateReplyPrompt(id, body)
      set((state) => ({
        replyPrompts: state.replyPrompts.map((p) => (p.id === id ? prompt : p)),
      }))
      return prompt
    } catch (err) {
      set({ error: errorMessage(err) })
      return null
    }
  },

  deleteReplyPrompt: async (id) => {
    set({ error: null })
    try {
      await api.deleteReplyPrompt(id)
      set((state) => ({ replyPrompts: state.replyPrompts.filter((p) => p.id !== id) }))
      if (useDraftStore.getState().customPromptId === id) {
        useDraftStore.getState().setCustomPromptId(null)
      }
      return true
    } catch (err) {
      set({ error: errorMessage(err) })
      return false
    }
  },

  /**
   * 把當下的選擇存成 Viewer 預設。
   *
   * `NONE_ID`（0）與 `null` 都要轉成 `null` 送出：在 `PATCH /preferences` 上
   * `null` 是「清除偏好」的意思（與 draft request 的語意不同，見 lib/api.ts）。
   */
  saveDefaults: async () => {
    set({ error: null })
    const draft = useDraftStore.getState()
    try {
      await api.updatePreferences({
        default_reply_tone: draft.toneId || null,
        default_persona_id: draft.personaId || null,
        default_reply_prompt_id: draft.customPromptId || null,
        default_sepia_enabled: draft.sepiaEnabled,
      })
      return true
    } catch (err) {
      set({ error: errorMessage(err) })
      return false
    }
  },

  clearError: () => set({ error: null }),
}))
