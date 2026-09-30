import type { Breakpoint } from '@/hooks/useBreakpoint'
import type { WorkbenchView } from '@/lib/modules'

/**
 * 哪些工作台是「表格型」（設計規格 2026-10-01 §1：清單型模組改表格、
 * 對話型模組保留主從式）。
 *
 * 表格型＝清單獨占整個主體、點一列整頁切到詳情——也就是把 768–1024 的主從
 * 切換套用到**所有寬度**。對話型（Mention 收件匣）在 ≥1024 仍是清單與工作區
 * 並排，因為 Draft Reply 需要一邊看清單一邊寫。
 *
 * 用 `Record<WorkbenchView, boolean>` 而不是一個 Set：加第四個工作台時漏填
 * 是編譯錯誤，不會安靜地被當成對話型。AppShell、MasterDetailBack 與
 * `router/useRouteSync.ts`（表格型的 `#/worklog` 不補回日期）都從這裡讀。
 * 工時草稿在 S4 改成表格型（一列一天）。
 */
export const TABLE_WORKBENCH: Record<WorkbenchView, boolean> = {
  summary: true,
  mentions: false,
  worklog: true,
}

export interface WorkbenchLayout {
  /** 清單那一半收起來（留著掛載，inert ＋ 透明，不是 display:none） */
  listHidden: boolean
  /** 工作區那一半收起來（同上） */
  mainHidden: boolean
  /** 清單獨占整個主體寬度（表格型，或主從切換寬度下的清單） */
  listFullWidth: boolean
  /** 證據欄收起來：表格獨占主體時，旁邊掛一條與任何一列都無關的證據欄只是噪音 */
  evidenceHidden: boolean
  /** 返回列在所有寬度都出現（表格型），否則只在主從切換寬度出現 */
  backEverywhere: boolean
}

/**
 * 版面由三個輸入決定：哪個工作台、網址有沒有指定一筆（`hasDetail`）、斷點。
 *
 * **判準直接來自網址，沒有另外一個「現在在主還是從」的狀態**——那是
 * AppShell 原本就在領的「選 URL 路由的額外報酬」（設計規格 §12），這裡照舊。
 */
export function workbenchLayout(
  view: WorkbenchView,
  detail: boolean,
  breakpoint: Breakpoint,
): WorkbenchLayout {
  const table = TABLE_WORKBENCH[view]
  const masterDetail = table || breakpoint === 'narrow' || breakpoint === 'tiny'
  return {
    listHidden: masterDetail && detail,
    mainHidden: masterDetail && !detail,
    listFullWidth: masterDetail,
    evidenceHidden: table && !detail,
    backEverywhere: table,
  }
}
