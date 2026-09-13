import { create } from 'zustand'

/**
 * 工時草稿的前端狀態。
 *
 * ### 為什麼不叫 `draft`
 *
 * `store/draft.ts` 與 `components/draft/` 已經是 **Draft Reply** 的地盤。兩者
 * 中文都叫「草稿」，但寫回去的地方完全不同：一個送進 Google Chat 的討論串，
 * 一個寫進 ZPlanner 的工時、會進月結、事後要改得走 correction-request。
 * 共用一個名字遲早會有人 import 錯，而且錯了不會編譯失敗。
 *
 * ### 階段 2a 的範圍
 *
 * 目前只有「在看哪一天」。週檢視資料、草稿產生的 SSE 串流、時數分配與送出
 * 都是階段 2b／2c 的事，那時這裡會長出 `days: Record<ISODate, DayDraft>`，
 * 每一天帶一個明確的 status 聯集（absent → generating → drafted → allocated
 * → ready → sending → submitted／partial／failed）。
 *
 * **刻意不沿用 `draft.ts` 的布林組合**（`streaming` + `raw` + `error` 湊出狀態）：
 * 那個 store 一次只裝一則 Mention，布林夠用；這個 store 一次裝七天，
 * 布林會讓「到底哪一天在串流」變成不可判定。
 */
interface WorklogState {
  /** `YYYY-MM-DD`。`null` ＝週檢視，沒有指定哪一天（不是「今天」） */
  selectedDate: string | null
  selectDate: (date: string | null) => void
  reset: () => void
}

export const useWorklogStore = create<WorklogState>((set) => ({
  selectedDate: null,
  selectDate: (selectedDate) => set({ selectedDate }),
  reset: () => set({ selectedDate: null }),
}))
