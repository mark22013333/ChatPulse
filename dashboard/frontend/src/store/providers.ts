import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import type { AIConfig, AIProvider } from '@/lib/types'

/**
 * 「讓伺服器決定」的哨兵值。選它時送出的 body **不帶** `provider` 欄位，
 * 伺服器就會照使用者偏好或自己的預設去解析。
 *
 * 為什麼一定要有這個選項：`/providers` 的 `default` 可能是別名（`claude`、`auto`），
 * 對不上任何一個 `providers[].name`，硬塞進下拉選單會變成一個選不到的值。
 */
export const AUTO_PROVIDER = '__auto__'

/**
 * 「跟隨預設」的 model 哨兵值。選它時送出的 body **不帶** `model` 欄位，
 * 由後端依序查 Viewer 偏好、再退回該供應商的 config 預設。
 *
 * 為什麼一定要有：沒有這個值的話，畫面一載入就得替 Viewer 挑一個具體 model
 * 送出去——那會讓「從來沒選過 model」的人突然開始指定 model，行為與改版前不同。
 */
export const INHERIT_MODEL = '__inherit__'

/**
 * 「自訂…」哨兵值。選它會冒出文字輸入框，實際送出的是框裡的裸值。
 *
 * 後端的 `models` 是常用值、不是完整列舉（新模型隨時會出現，而 `claude --help`
 * 根本沒給完整清單），所以一定要留一條讓 Viewer 自己打字的路。
 */
export const CUSTOM_MODEL = '__custom__'

/** 找得到、而且可用，才適合當作預選值。 */
function isSelectable(providers: AIProvider[], name: string | null | undefined): boolean {
  if (!name) return false
  return providers.some((item) => item.name === name && item.available)
}

/**
 * 決定初始選擇：使用者偏好 → 伺服器預設 → 交給伺服器決定。
 * 偏好或伺服器預設若是別名、或指到已經不可用的供應商，一律退回 AUTO。
 */
export function resolveSelected(
  providers: AIProvider[],
  serverDefault: string | null,
  savedDefault: string | null,
): string {
  if (savedDefault) return isSelectable(providers, savedDefault) ? savedDefault : AUTO_PROVIDER
  return isSelectable(providers, serverDefault) ? serverDefault! : AUTO_PROVIDER
}

/** 供應商名稱 → label（找不到就退回原名，例如伺服器回了前端還不認得的供應商）。 */
export function providerLabel(providers: AIProvider[], name: string | null | undefined): string {
  if (!name) return ''
  return providers.find((item) => item.name === name)?.label ?? name
}

/** 某個供應商的常用裸值清單（認不得的供應商回空陣列，不要讓呼叫端拿到 undefined）。 */
export function modelChoices(providers: AIProvider[], name: string | null | undefined): string[] {
  if (!name) return []
  return providers.find((item) => item.name === name)?.models ?? []
}

/**
 * 某個供應商目前該顯示在下拉上的值。
 *
 * 順序：這次選的 → 已存的偏好 → 跟隨預設。偏好的裸值不在常用清單裡時
 * （Viewer 自己打過的值）要顯示成「自訂」，否則那個下拉會選不到任何一項，
 * 看起來像偏好沒存到。
 */
export function resolveModelValue(
  providers: AIProvider[],
  savedModels: Record<string, string>,
  selection: Record<string, string>,
  providerName: string,
): string {
  const picked = selection[providerName]
  if (picked) return picked
  const saved = savedModels[providerName]
  if (!saved) return INHERIT_MODEL
  return modelChoices(providers, providerName).includes(saved) ? saved : CUSTOM_MODEL
}

/**
 * 自訂輸入框該顯示的文字。
 *
 * 沒打過字時，用「白名單外的已存偏好」當初始內容——那個值本來就是 Viewer
 * 自己打進去的，重新打開畫面時讓它留在框裡，才不會看起來像被清掉了。
 */
export function resolveModelDraft(
  providers: AIProvider[],
  savedModels: Record<string, string>,
  drafts: Record<string, string>,
  providerName: string,
): string {
  const draft = drafts[providerName]
  if (draft !== undefined) return draft
  const saved = savedModels[providerName]
  if (!saved || modelChoices(providers, providerName).includes(saved)) return ''
  return saved
}

/**
 * 某個供應商這次實際要送出的裸值；`null` 代表不送 `model` 欄位。
 *
 * 選了「自訂」但框是空的一樣回 `null`——空字串送過去不是「用預設」，
 * 是一個後端無法辨識的 model 名。
 */
export function effectiveModel(
  providers: AIProvider[],
  savedModels: Record<string, string>,
  selection: Record<string, string>,
  drafts: Record<string, string>,
  providerName: string,
): string | null {
  const value = resolveModelValue(providers, savedModels, selection, providerName)
  if (value === INHERIT_MODEL) return null
  if (value !== CUSTOM_MODEL) return value
  const draft = resolveModelDraft(providers, savedModels, drafts, providerName).trim()
  return draft || null
}

/** 偏好只存得下字串，把「清除」用的 null 濾掉再放進 store。 */
function withoutCleared(mapping: Record<string, string | null>): Record<string, string> {
  const result: Record<string, string> = {}
  for (const [key, value] of Object.entries(mapping)) {
    if (value) result[key] = value
  }
  return result
}

interface ProviderState {
  providers: AIProvider[]
  /** 伺服器預設，可能是別名，不保證對得上 providers 裡的 name */
  serverDefault: string | null
  /** 已經存進偏好的供應商；null＝沿用伺服器預設 */
  savedDefault: string | null
  /** 目前選擇：AUTO_PROVIDER 或某個 providers[].name */
  selected: string
  /**
   * 已經存進偏好的 model 裸值：`{provider實作名: 裸值}`。沒設過的不在裡面。
   *
   * 一個 provider 一格，換來換去都記得住——這正是「切換供應商時 model 要跟著
   * 切回該供應商的偏好」能成立的原因。
   */
  savedModels: Record<string, string>
  /** 這次選的 model：`{provider實作名: 裸值／INHERIT_MODEL／CUSTOM_MODEL}` */
  modelSelection: Record<string, string>
  /** 自訂輸入框的內容：`{provider實作名: Viewer 打的裸值}` */
  modelDrafts: Record<string, string>
  /** 是否已由 /me 或 /providers 初始化過（避免 me 每次刷新就蓋掉使用者當下的選擇） */
  initialised: boolean
  loading: boolean
  saving: boolean
  /** model 偏好存檔中。與 `saving` 分開，否則存 model 會讓供應商那顆按鈕也轉圈 */
  savingModel: boolean
  error: string | null

  loadProviders: () => Promise<void>
  applyServerConfig: (
    ai: AIConfig | null | undefined,
    savedDefault: string | null | undefined,
    savedModels?: Record<string, string> | null,
  ) => void
  setSelected: (value: string) => void
  saveAsDefault: () => Promise<boolean>
  setModel: (providerName: string, value: string) => void
  setModelDraft: (providerName: string, text: string) => void
  saveModelAsDefault: (providerName: string) => Promise<boolean>
}

export const useProviderStore = create<ProviderState>((set, get) => ({
  providers: [],
  serverDefault: null,
  savedDefault: null,
  selected: AUTO_PROVIDER,
  savedModels: {},
  modelSelection: {},
  modelDrafts: {},
  initialised: false,
  loading: false,
  saving: false,
  savingModel: false,
  error: null,

  loadProviders: async () => {
    if (get().loading) return
    set({ loading: true })
    try {
      const data = await api.providers()
      // model 偏好要跟 savedDefault 一樣保留下來：`/providers` 未登入也能打，
      // 回應本來就沒有 default_models，不傳的話會把 /me 帶回來的偏好清成空的
      // ——資料其實還在後端，畫面上卻不見了，這種掉法最難察覺。
      // 回應真的帶了就以它為準（日後後端補上這個鍵時不必再改這裡）。
      get().applyServerConfig(data, get().savedDefault, data.default_models ?? get().savedModels)
    } catch {
      // 拿不到清單就只留「自動」一個選項，不擋主流程
    } finally {
      set({ loading: false })
    }
  },

  applyServerConfig: (ai, savedDefault, savedModels) => {
    if (!ai?.providers?.length) return
    const normalisedSaved = savedDefault ?? null
    // 偏好的來源以呼叫端給的為準（走 /me 的 preferences），沒給才看 ai 裡那份。
    // `/providers` 未登入也能打，那條路本來就不會有偏好。
    const models = savedModels ?? ai.default_models ?? {}
    set((state) => ({
      providers: ai.providers,
      serverDefault: ai.default ?? null,
      savedDefault: normalisedSaved,
      savedModels: models,
      // 只在第一次決定選擇；之後 me 再刷新也不覆寫使用者當下選的
      selected: state.initialised
        ? state.selected
        : resolveSelected(ai.providers, ai.default ?? null, normalisedSaved),
      initialised: true,
    }))
  },

  setSelected: (value) => set({ selected: value, error: null }),

  saveAsDefault: async () => {
    const { selected } = get()
    // AUTO 代表「不要有偏好」，對應後端的 null
    const value = selected === AUTO_PROVIDER ? null : selected
    set({ saving: true, error: null })
    try {
      const prefs = await api.updatePreferences({ default_provider: value })
      set({ savedDefault: prefs?.default_provider ?? value })
      return true
    } catch (err) {
      set({ error: errorMessage(err) })
      return false
    } finally {
      set({ saving: false })
    }
  },

  setModel: (providerName, value) =>
    set((state) => ({
      modelSelection: { ...state.modelSelection, [providerName]: value },
      error: null,
    })),

  setModelDraft: (providerName, text) =>
    set((state) => ({
      modelDrafts: { ...state.modelDrafts, [providerName]: text },
      error: null,
    })),

  saveModelAsDefault: async (providerName) => {
    const { providers, savedModels, modelSelection, modelDrafts } = get()
    const value = effectiveModel(providers, savedModels, modelSelection, modelDrafts, providerName)

    // 🔴 送**整份**偏好，不是只送異動的那一格。
    // 契約第三節寫「值送 null 表示清除」（讀起來像逐格合併），第二節的
    // repository 函式卻寫「整份覆寫」——兩種語意下，送整份的結果都正確；
    // 只送一格則在「整份覆寫」語意下會把其他供應商的偏好一起洗掉。
    // 被清除的那一格明寫 null，合併語意下才真的清得掉。
    const payload: Record<string, string | null> = { ...savedModels, [providerName]: value }

    set({ savingModel: true, error: null })
    try {
      const prefs = await api.updatePreferences({ default_models: payload })
      set({ savedModels: prefs?.default_models ?? withoutCleared(payload) })
      return true
    } catch (err) {
      set({ error: errorMessage(err) })
      return false
    } finally {
      set({ savingModel: false })
    }
  },
}))

/**
 * 串流 request body 要不要帶 `provider`。
 * 回傳 `{}` 時代表交給伺服器決定——這與「帶一個猜出來的名字」語意不同，不可混用。
 */
export function providerRequestField(): { provider?: string } {
  const { selected } = useProviderStore.getState()
  return selected && selected !== AUTO_PROVIDER ? { provider: selected } : {}
}

/**
 * 串流 request body 要不要帶 `model`。帶的是**裸值**（`opus`），
 * 不是用量欄位那種帶前綴的識別字（`claude-cli:opus`）。
 *
 * 🔴 供應商選「自動」時一律不帶：`auto` 會自己依序試供應商，指定的 model
 * 未必適用於它被挑中的那一個，所以後端用 400 擋下這個組合。這裡多送一個
 * 欄位，換來的是整次串流直接失敗。
 */
export function modelRequestField(): { model?: string } {
  const { providers, selected, savedModels, modelSelection, modelDrafts } =
    useProviderStore.getState()
  if (!selected || selected === AUTO_PROVIDER) return {}
  const model = effectiveModel(providers, savedModels, modelSelection, modelDrafts, selected)
  return model ? { model } : {}
}
