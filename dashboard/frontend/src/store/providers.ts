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

interface ProviderState {
  providers: AIProvider[]
  /** 伺服器預設，可能是別名，不保證對得上 providers 裡的 name */
  serverDefault: string | null
  /** 已經存進偏好的供應商；null＝沿用伺服器預設 */
  savedDefault: string | null
  /** 目前選擇：AUTO_PROVIDER 或某個 providers[].name */
  selected: string
  /** 是否已由 /me 或 /providers 初始化過（避免 me 每次刷新就蓋掉使用者當下的選擇） */
  initialised: boolean
  loading: boolean
  saving: boolean
  error: string | null

  loadProviders: () => Promise<void>
  applyServerConfig: (ai: AIConfig | null | undefined, savedDefault: string | null | undefined) => void
  setSelected: (value: string) => void
  saveAsDefault: () => Promise<boolean>
}

export const useProviderStore = create<ProviderState>((set, get) => ({
  providers: [],
  serverDefault: null,
  savedDefault: null,
  selected: AUTO_PROVIDER,
  initialised: false,
  loading: false,
  saving: false,
  error: null,

  loadProviders: async () => {
    if (get().loading) return
    set({ loading: true })
    try {
      const data = await api.providers()
      get().applyServerConfig(data, get().savedDefault)
    } catch {
      // 拿不到清單就只留「自動」一個選項，不擋主流程
    } finally {
      set({ loading: false })
    }
  },

  applyServerConfig: (ai, savedDefault) => {
    if (!ai?.providers?.length) return
    const normalisedSaved = savedDefault ?? null
    set((state) => ({
      providers: ai.providers,
      serverDefault: ai.default ?? null,
      savedDefault: normalisedSaved,
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
}))

/**
 * 串流 request body 要不要帶 `provider`。
 * 回傳 `{}` 時代表交給伺服器決定——這與「帶一個猜出來的名字」語意不同，不可混用。
 */
export function providerRequestField(): { provider?: string } {
  const { selected } = useProviderStore.getState()
  return selected && selected !== AUTO_PROVIDER ? { provider: selected } : {}
}
