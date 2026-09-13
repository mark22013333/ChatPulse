/**
 * 模組表——「這個 app 有哪些頂層工作台」的**唯一**事實來源。
 *
 * ### 為什麼要有這張表
 *
 * 在此之前，新增一個頂層工作台要改 8 個地方：`route.ts` 的 `Section` 聯集型別、
 * `AppShell` 裡三處手寫的 `<Pane>` 配對、`TopBar` 的頁籤、`commands.ts` 的
 * 「前往」清單、`hotkeys.ts` 的 `g` 前綴表、新 store、`useRouteSync`。
 * 漏掉任何一處都不會編譯失敗，只會在執行期少一塊。
 *
 * 現在：**在這張表加一筆**，導航、命令面板、快捷鍵、麵包屑全部跟著長出來。
 * 還是要自己寫的只有兩件事——該模組的畫面本體，以及它在 `parseHash` 裡
 * 怎麼解析自己的網址參數（每個模組的參數形狀本來就不同，硬抽象反而更難讀）。
 *
 * ### 為什麼這個檔不能 import 圖示
 *
 * `route.ts` 要從這裡拿 `Section` 型別，而 `route.ts` 的契約是**零 React 相依、
 * 可以在 node 環境直接測**（見該檔檔頭）。所以這一層只放純資料；
 * 圖示在 `app/nav/registry.tsx` 才接上去。
 *
 * ### status 的意義
 *
 * `'ready'` 的模組才進 `Section` 型別，也才是路由認得的網址。
 * `'planned'` 只在導航上佔一個灰掉的位子，告訴使用者「這裡以後會有東西」——
 * 這是刻意的：導航的形狀應該反映產品的完整樣貌，不是只反映已經寫完的部分。
 * 要讓它上線就把 `status` 改成 `'ready'`，`Section` 型別會自動接納它，
 * 沒接上 `parseHash` 的話 TypeScript 會在 `AppShell` 那裡報出來。
 */

export interface NavModule {
  readonly id: string
  /** 導航展開時與麵包屑用的全名 */
  readonly label: string
  /** icon rail 收合時圖示下方的兩字標籤 */
  readonly shortLabel: string
  /** 點下去要去哪。planned 的模組不會被點到，填 '' */
  readonly hash: string
  readonly status: 'ready' | 'planned'
  /** 導航的分組標題。同一組要連續排在一起 */
  readonly group: string
  /** `g` 前綴快捷鍵的第二個鍵（`g s` 去摘要）。沒有就不註冊 */
  readonly hotkey?: string
  /** planned 專用：一句話說明它以後會做什麼，寫在導航上（不准用 tooltip） */
  readonly plannedNote?: string
}

export const NAV_MODULES = [
  {
    id: 'summary',
    label: '摘要工作台',
    shortLabel: '摘要',
    hash: '#/summary',
    status: 'ready',
    group: '工作',
    hotkey: 's',
  },
  {
    id: 'mentions',
    label: 'Mention 收件匣',
    shortLabel: '收件',
    hash: '#/mentions',
    status: 'ready',
    group: '工作',
    hotkey: 'm',
  },
  {
    id: 'stats',
    label: '統計',
    shortLabel: '統計',
    hash: '',
    status: 'planned',
    group: '分析',
    plannedNote: '即將推出',
  },
  {
    id: 'weekly',
    label: '週報',
    shortLabel: '週報',
    hash: '',
    status: 'planned',
    group: '分析',
    plannedNote: '即將推出',
  },
  {
    id: 'settings',
    label: '設定',
    shortLabel: '設定',
    hash: '#/settings/reply',
    status: 'ready',
    group: '系統',
    hotkey: ',',
  },
] as const satisfies readonly NavModule[]

/** 已上線的模組 id ——這就是路由認得的 section 全集。 */
export type Section = Extract<(typeof NAV_MODULES)[number], { status: 'ready' }>['id']

/** 包含尚未上線的，用於導航渲染。 */
export type ModuleId = (typeof NAV_MODULES)[number]['id']

export const READY_MODULES = NAV_MODULES.filter(
  (m): m is Extract<(typeof NAV_MODULES)[number], { status: 'ready' }> => m.status === 'ready',
)

/** 導航分組的顯示順序，由 NAV_MODULES 的排列推導，不另外維護一份。 */
export const NAV_GROUPS: readonly string[] = [...new Set(NAV_MODULES.map((m) => m.group))]

export function moduleById(id: string): NavModule | undefined {
  return NAV_MODULES.find((m) => m.id === id)
}

/** 給麵包屑與命令面板用：這個 section 的全名。 */
export function labelOf(section: Section): string {
  return moduleById(section)?.label ?? section
}
