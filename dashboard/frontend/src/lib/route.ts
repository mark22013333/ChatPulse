/**
 * Hash 路由的純函式層（設計規格 §6）。
 *
 * **為什麼是 hash 而不是 History API**：`vite.config.ts` 的 `base: './'`
 * 讓建置產物用相對路徑引用 asset，而後端的 `spa_fallback` 對任何找不到的
 * 檔案都回 `index.html`。兩者相乘的結果是：History 路由深連結到
 * `/summary/AAAA` 之後重新整理，瀏覽器會把 `./assets/index-*.js` 解析成
 * `/summary/assets/index-*.js` → fallback 回 HTML → module script 的
 * MIME 錯誤 → 白畫面。hash 之外的 path 永遠是 `/`，asset 一律解析到
 * `/assets/`，零改動。
 *
 * 這個檔沒有任何 React 或 store 相依，可以在 node 環境直接測。
 */

/**
 * `Section` 的定義搬到 `lib/modules.ts` 了——模組表是「有哪些頂層工作台」的
 * 唯一事實來源，型別由它推導，不要在這裡再維護一份聯集。
 *
 * 這裡 re-export 是為了相容：既有的 `import { type Section } from '@/lib/route'`
 * 不必全部改。**type-only re-export 不產生執行期 import**，所以這個檔
 * 「零 React／零 store 相依、可在 node 環境直接測」的契約沒有被破壞。
 */
export type { Section } from '@/lib/modules'
import type { Section } from '@/lib/modules'

export const SETTINGS_TABS = [
  'reply',
  'personas',
  'prompts',
  'code-projects',
  'work-repos',
  'spaces',
  'data',
  'diagnostics',
] as const

export type SettingsTab = (typeof SETTINGS_TABS)[number]

export interface Route {
  section: Section
  /** `#/summary/:spaceKey` 的 spaceKey，已經還原成完整的 `spaces/xxx` */
  spaceId: string | null
  /** `#/mentions/:mentionId` */
  mentionId: number | null
  /** `?merge=47,48`——順序有意義，第一個是主要那則 */
  mergeIds: number[]
  /**
   * `#/worklog/2026-09-11` 的日期，`YYYY-MM-DD`。
   *
   * `null` 是「沒有指定哪一天」（週檢視），**不是**「今天」——今天要由呼叫端
   * 自己決定，因為它涉及時區，而這個檔的契約是零相依的純函式。
   */
  worklogDate: string | null
  settingsTab: SettingsTab | null
  /** `#/settings/personas/:id` 的 id，保持字串（有些是數字有些不是） */
  settingsId: string | null
  query: Record<string, string>
}

const SPACE_PREFIX = 'spaces/'

/**
 * Space id → 網址片段。
 *
 * `spaces/AAAAxLxqJxY` 裡的斜線會被當成路徑分隔，所以砍掉固定前綴。
 * 436 個實測 id 都是這個形狀；不合形狀的退回百分比編碼，不要讓它壞掉。
 */
export function toSpaceKey(id: string): string {
  if (!id) return ''
  if (id.startsWith(SPACE_PREFIX)) return encodeURIComponent(id.slice(SPACE_PREFIX.length))
  return encodeURIComponent(id)
}

/** 網址片段 → Space id。與 `toSpaceKey` 互為反函式。 */
export function fromSpaceKey(key: string): string {
  if (!key) return ''
  const decoded = safeDecode(key)
  // 已經自己帶了前綴（使用者手打或舊連結）就不要疊第二層
  return decoded.startsWith(SPACE_PREFIX) ? decoded : SPACE_PREFIX + decoded
}

function safeDecode(value: string): string {
  try {
    return decodeURIComponent(value)
  } catch {
    // 壞掉的百分比編碼不該讓整個路由掛掉
    return value
  }
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

/**
 * 驗 `YYYY-MM-DD`，不合法回 `null`。
 *
 * **光靠 regex 不夠**：`2026-02-30`、`2026-13-01` 都符合形狀但不存在。而
 * `new Date('2026-02-30')` 會自己進位成 3/2，不會報錯——網址上的一個錯字
 * 就這樣變成另一天的工時，而且畫面上完全看不出來。
 *
 * 逐欄檢查，全程用 UTC 算月底：`Date.UTC(y, m, 0)` 的 month 是 0-based，
 * 傳 1-based 的 m 就是下個月、day 給 0 就退回這個月的最後一天。用 UTC 是
 * 為了不讓執行環境的時區影響「這個月有幾天」這種與時區無關的事實。
 */
export function parseWorkDate(value: string | undefined | null): string | null {
  if (!value || !ISO_DATE.test(value)) return null
  const [y, m, d] = value.split('-').map(Number)
  if (m < 1 || m > 12) return null
  const lastDay = new Date(Date.UTC(y, m, 0)).getUTCDate()
  if (d < 1 || d > lastDay) return null
  return value
}

function parseIds(raw: string | undefined): number[] {
  if (!raw) return []
  const seen = new Set<number>()
  const out: number[] = []
  for (const part of raw.split(',')) {
    const n = Number(part.trim())
    if (Number.isInteger(n) && n > 0 && !seen.has(n)) {
      seen.add(n)
      out.push(n)
    }
  }
  return out
}

export const DEFAULT_ROUTE: Route = {
  section: 'summary',
  spaceId: null,
  mentionId: null,
  mergeIds: [],
  worklogDate: null,
  settingsTab: null,
  settingsId: null,
  query: {},
}

/**
 * 解析 `location.hash`。任何看不懂的東西一律退回摘要工作台——
 * 路由壞掉時給一個空白畫面比給一個錯誤訊息更難查。
 */
export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, '')
  const [pathPart, queryPart] = raw.split('?')
  const query: Record<string, string> = {}
  if (queryPart) {
    for (const [k, v] of new URLSearchParams(queryPart)) query[k] = v
  }

  const segments = pathPart.split('/').filter(Boolean)
  const [head, second, third] = segments

  if (head === 'mentions') {
    const id = Number(second)
    return {
      ...DEFAULT_ROUTE,
      section: 'mentions',
      mentionId: Number.isInteger(id) && id > 0 ? id : null,
      mergeIds: parseIds(query.merge),
      query,
    }
  }

  if (head === 'worklog') {
    return {
      ...DEFAULT_ROUTE,
      section: 'worklog',
      // 日期不合法時退回週檢視（null），**不要**猜成今天：使用者手打錯一個
      // 數字就被帶到另一天，而工時是會寫進外部系統的東西
      worklogDate: parseWorkDate(second),
      query,
    }
  }

  if (head === 'settings') {
    const tab = (SETTINGS_TABS as readonly string[]).includes(second ?? '')
      ? (second as SettingsTab)
      : 'reply'
    return {
      ...DEFAULT_ROUTE,
      section: 'settings',
      settingsTab: tab,
      settingsId: third ? safeDecode(third) : null,
      query,
    }
  }

  // 其餘（含空字串、`/`、看不懂的）一律是摘要工作台
  return {
    ...DEFAULT_ROUTE,
    section: 'summary',
    spaceId: head === 'summary' && second ? fromSpaceKey(second) : null,
    query,
  }
}

function withQuery(path: string, query?: Record<string, string | undefined>): string {
  if (!query) return path
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== '') params.set(k, v)
  }
  const qs = params.toString()
  return qs ? `${path}?${qs}` : path
}

export function hashForSummary(
  spaceId?: string | null,
  query?: Record<string, string | undefined>,
): string {
  return withQuery(spaceId ? `#/summary/${toSpaceKey(spaceId)}` : '#/summary', query)
}

export function hashForMentions(mentionId?: number | null, mergeIds: number[] = []): string {
  const path = mentionId ? `#/mentions/${mentionId}` : '#/mentions'
  return withQuery(path, mergeIds.length > 1 ? { merge: mergeIds.join(',') } : undefined)
}

/**
 * `#/worklog` 或 `#/worklog/2026-09-11`。
 *
 * 不合法的日期一律退成週檢視的網址，不會產生一個指向不存在日期的連結。
 */
export function hashForWorklog(date?: string | null): string {
  const valid = parseWorkDate(date)
  return valid ? `#/worklog/${valid}` : '#/worklog'
}

export function hashForSettings(tab: SettingsTab = 'reply', id?: string | number | null): string {
  return id === undefined || id === null || id === ''
    ? `#/settings/${tab}`
    : `#/settings/${tab}/${encodeURIComponent(String(id))}`
}

/**
 * 這個位置有沒有「選中某個東西」。
 *
 * 768–1024 的主從切換靠它決定顯示清單還是工作區（設計規格 §12）。
 *
 * 每個工作台的 detail 是不同欄位（Space／Mention／日期），所以這裡必須逐一
 * 列出。原本寫在 `AppShell` 裡是一行二元三元式，加第三個工作台時它會安靜地
 * 落到 summary 那一支——`route.spaceId` 在工時模組永遠是 null，於是那個寬度
 * 下**永遠回不到工作區**。
 */
export function hasDetail(route: Route): boolean {
  switch (route.section) {
    case 'mentions':
      return route.mentionId !== null
    case 'worklog':
      return route.worklogDate !== null
    case 'summary':
      return route.spaceId !== null
    // 設定是覆蓋層，本身沒有主從之分
    case 'settings':
      return false
    default: {
      const exhaustive: never = route.section
      void exhaustive
      return false
    }
  }
}

/** 兩個 route 是不是同一個位置（用來決定 push 還是 replace、要不要重跑同步）。 */
export function sameRoute(a: Route, b: Route): boolean {
  return (
    a.section === b.section &&
    a.spaceId === b.spaceId &&
    a.mentionId === b.mentionId &&
    a.worklogDate === b.worklogDate &&
    a.settingsTab === b.settingsTab &&
    a.settingsId === b.settingsId &&
    a.mergeIds.length === b.mergeIds.length &&
    a.mergeIds.every((id, i) => id === b.mergeIds[i])
  )
}
