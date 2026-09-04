import { create } from 'zustand'
import { api, errorMessage, setUnauthorizedHandler } from '@/lib/api'
import type { AuthStatus, Me } from '@/lib/types'

interface AuthState {
  /** 初次載入 auth/status 是否還在進行 */
  booting: boolean
  status: AuthStatus | null
  me: Me | null
  /** login 會在伺服器端開瀏覽器，可能等 1~3 分鐘 */
  loginPending: boolean
  bootstrapPending: boolean
  error: string | null

  init: () => Promise<void>
  login: () => Promise<void>
  bootstrap: () => Promise<void>
  logout: () => Promise<void>
  refreshMe: () => Promise<void>
  clearSession: () => void
  clearError: () => void
}

export const useAuthStore = create<AuthState>((set, get) => ({
  booting: true,
  status: null,
  me: null,
  loginPending: false,
  bootstrapPending: false,
  error: null,

  init: async () => {
    set({ booting: true, error: null })
    try {
      const status = await api.authStatus()
      set({ status, booting: false })
      if (status.authenticated) await get().refreshMe()
    } catch (err) {
      set({ booting: false, error: errorMessage(err) })
    }
  },

  login: async () => {
    set({ loginPending: true, error: null })
    try {
      const result = await api.login()
      if (result.authenticated) {
        const status = await api.authStatus()
        set({ status })
        await get().refreshMe()
      } else {
        set({ error: '登入未完成，請再試一次' })
      }
    } catch (err) {
      set({ error: errorMessage(err) })
    } finally {
      set({ loginPending: false })
    }
  },

  bootstrap: async () => {
    set({ bootstrapPending: true, error: null })
    try {
      const result = await api.bootstrap()
      if (result.authenticated) {
        const status = await api.authStatus()
        set({ status })
        await get().refreshMe()
      } else {
        set({ error: '匯入既有憑證未成功' })
      }
    } catch (err) {
      set({ error: errorMessage(err) })
    } finally {
      set({ bootstrapPending: false })
    }
  },

  logout: async () => {
    try {
      await api.logout()
    } catch {
      // 即使後端清 session 失敗，前端仍要回到登入畫面
    }
    get().clearSession()
    const status = await api.authStatus().catch(() => null)
    if (status) set({ status })
  },

  refreshMe: async () => {
    try {
      const me = await api.me()
      set({ me })
    } catch (err) {
      set({ error: errorMessage(err) })
    }
  },

  clearSession: () => {
    set((state) => ({
      me: null,
      status: state.status ? { ...state.status, authenticated: false, viewer: null } : null,
    }))
  },

  clearError: () => set({ error: null }),
}))

/** 任何 API 回 401 時清空 session，畫面自動退回登入畫面。 */
setUnauthorizedHandler(() => {
  useAuthStore.getState().clearSession()
})
