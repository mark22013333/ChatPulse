import { create } from 'zustand'
import { api } from '@/lib/api'
import type { UsageRow } from '@/lib/types'

/** `GET /api/v1/usage` 的合法區間（與後端 Field(ge=1, le=90) 對齊）。 */
export const USAGE_DAYS_MIN = 1
export const USAGE_DAYS_MAX = 90
export const USAGE_DAYS_DEFAULT = 14

/** 設定頁提供的天數選項。後端接受 1~90 任意值，這裡只挑常用的幾檔。 */
export const USAGE_DAY_CHOICES = [7, 14, 30, 90] as const

interface UsageState {
  rows: UsageRow[]
  days: number
  loading: boolean
  loaded: boolean
  error: string | null

  /** 換天數並重新載入。超出 1~90 會被夾回區間內。 */
  setDays: (days: number) => Promise<void>
  load: () => Promise<void>
  /** 沒載入過才載入，重複呼叫是安全的。 */
  ensureLoaded: () => Promise<void>
}

function clampDays(days: number): number {
  if (!Number.isFinite(days)) return USAGE_DAYS_DEFAULT
  return Math.min(USAGE_DAYS_MAX, Math.max(USAGE_DAYS_MIN, Math.round(days)))
}

/**
 * Token 用量。
 *
 * 從 `UsagePanel` 的 useState 搬進 store，是因為天數要能從設定頁改——
 * 留在元件裡的話，設定頁改完面板不會知道。天數本身也存進 store，切頁籤
 * 回來不會跳回預設值。
 */
export const useUsageStore = create<UsageState>((set, get) => ({
  rows: [],
  days: USAGE_DAYS_DEFAULT,
  loading: false,
  loaded: false,
  error: null,

  load: async () => {
    set({ loading: true, error: null })
    try {
      const data = await api.usage(get().days)
      set({ rows: data.usage ?? [], loaded: true })
    } catch {
      // 用量是輔助資訊，載不到就顯示空的，不要用錯誤訊息打斷正在做的事
      set({ rows: [], error: '用量讀取失敗' })
    } finally {
      set({ loading: false })
    }
  },

  ensureLoaded: async () => {
    const { loaded, loading } = get()
    if (loaded || loading) return
    await get().load()
  },

  setDays: async (days) => {
    const next = clampDays(days)
    if (next === get().days && get().loaded) return
    set({ days: next })
    await get().load()
  },
}))

/** 這一批列的 token 總和。 */
export function totalTokens(rows: UsageRow[]): number {
  return rows.reduce((sum, row) => sum + (row.total_tokens ?? 0), 0)
}
