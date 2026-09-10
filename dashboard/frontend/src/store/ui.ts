import { create } from 'zustand'
import type { Breakpoint } from '@/hooks/useBreakpoint'

const STORAGE_KEY = 'chatpulse.evidenceDrawer'

/**
 * 抽屜開合**每個斷點各記一份**：在 1280 開著不代表 1024 也要開著——
 * 那兩種寬度下它是不同的東西（一個是版面的一部分，一個會蓋住草稿）。
 */
type DrawerState = Partial<Record<Breakpoint, boolean>>

function read(): DrawerState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as DrawerState) : {}
  } catch {
    // 隱私模式、清過站台資料、或瀏覽器擋 storage：當作沒設定過
    return {}
  }
}

function write(state: DrawerState) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {
    // 存不進去頂多下次要再開一次，不值得打斷使用者
  }
}

interface UiState {
  drawer: DrawerState
  /** 命令面板開著嗎 */
  paletteOpen: boolean
  /** 快捷鍵說明（`?`）開著嗎 */
  helpOpen: boolean

  isDrawerOpen: (breakpoint: Breakpoint) => boolean
  toggleDrawer: (breakpoint: Breakpoint) => void
  closeDrawer: (breakpoint: Breakpoint) => void
  setPaletteOpen: (open: boolean) => void
  setHelpOpen: (open: boolean) => void
}

export const useUiStore = create<UiState>((set, get) => ({
  // 初始值在 module 載入時讀一次。node 環境沒有 localStorage，read() 會回 {}
  drawer: typeof window === 'undefined' ? {} : read(),
  paletteOpen: false,
  helpOpen: false,

  // 預設**關著**：抽屜會蓋住正在讀的草稿，不該自己跳出來
  isDrawerOpen: (breakpoint) => get().drawer[breakpoint] === true,

  toggleDrawer: (breakpoint) =>
    set((state) => {
      const next = { ...state.drawer, [breakpoint]: !state.drawer[breakpoint] }
      write(next)
      return { drawer: next }
    }),

  closeDrawer: (breakpoint) =>
    set((state) => {
      if (!state.drawer[breakpoint]) return state
      const next = { ...state.drawer, [breakpoint]: false }
      write(next)
      return { drawer: next }
    }),

  setPaletteOpen: (paletteOpen) => set({ paletteOpen }),

  setHelpOpen: (helpOpen) => set({ helpOpen }),
}))

/**
 * 有沒有一層「暫時蓋在所有東西上面」的覆蓋層開著。
 *
 * 用途是 Esc 的由內而外關閉（規格 §11.1）：設定中心的 Esc 監聽掛在 window 上，
 * 它必須知道自己是不是被蓋住了。判準集中在這裡，是因為漏掉其中一個的症狀
 * 很難查——按一次 Esc 同時關掉兩層，而且兩層都「看起來有反應」。
 * 2026-09-10 加入 `?` 說明面板時，就是靠這個 export 讓設定不必知道有它。
 */
export function isOverlayOpen(): boolean {
  const { paletteOpen, helpOpen } = useUiStore.getState()
  return paletteOpen || helpOpen
}
