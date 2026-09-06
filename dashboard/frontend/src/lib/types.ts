// 依 docs/api-contract.md（v2）逐條對應的型別定義。這裡是前端對後端契約的唯一映射處。

export interface Viewer {
  id: number
  google_user_id: string
  email: string
  display_name: string
}

export interface AuthStatus {
  authenticated: boolean
  viewer: Viewer | null
  legacy_token_available: boolean
  can_bootstrap: boolean
  viewer_count: number
}

export interface AuthResult {
  authenticated: boolean
  viewer?: Viewer | null
}

export interface Preferences {
  pinned_space_ids: string[]
  default_limit: number
  default_style: SummaryStyleValue
  /** 使用者偏好的 AI 供應商；null＝沿用伺服器預設 */
  default_provider?: string | null
}

/**
 * 單一 AI 供應商（`GET /api/v1/providers` 與 `/me` 的 `ai.providers`）。
 * `name` 是實作名稱（claude_cli / gemini），送 request 時用它。
 * `available: false` 的供應商仍要顯示，`reason` 寫的是「該設哪個環境變數」，
 * 對使用者有用，不可以吞掉。
 */
export interface AIProvider {
  name: string
  label: string
  model: string
  available: boolean
  reason: string
}

/** `GET /api/v1/providers` 的回應，也是 `/me` 的 `ai` 欄位內容。 */
export interface AIConfig {
  /**
   * 伺服器預設。可能是別名（`claude`／`auto`，會挑第一個可用的實作），
   * 也可能直接是實作名稱之一——所以不保證對得上 `providers` 裡的任何 `name`。
   */
  default: string
  providers: AIProvider[]
}

export interface CollectorRunStats {
  new_mentions?: number
  total_matched?: number
  spaces_polled?: number
  spaces_total?: number
  api_calls?: number
  elapsed_seconds?: number
  full_sweep?: boolean
  errors?: string[]
}

export interface CollectorStatus {
  implementation: string
  interval_seconds: number
  running: boolean
  last_polled_at: string | null
  last_error: string | null
  last_run_stats: CollectorRunStats | null
}

export interface MentionCounts {
  pending: number
  resolved: number
}

export interface Me {
  viewer: Viewer
  scopes: string[]
  has_identity_scope: boolean
  preferences: Preferences
  collector: CollectorStatus | null
  mention_counts: MentionCounts
  /** 登入後可直接用這份供應商設定，不必另外打 `/providers` */
  ai?: AIConfig | null
}

export interface Space {
  id: string
  displayName: string
  type: string
  /**
   * Google 的 `spaceThreadingState`。**不可單獨拿它判斷有沒有討論串**：
   * 實測 436 個 Space，私訊回報的是 `THREADED_MESSAGES` 而不是文件寫的
   * `UNTHREADED_MESSAGES`。判準用 `isFlatSpace()`（與後端 draft_context 一致）。
   */
  threadingState?: string | null
  lastActiveTime: string | null
  memberCount?: number | null
  pinned?: boolean
  /** 這個空間沒有官方名稱（私訊／未命名），可以自己取一個 */
  renamable?: boolean
  /** 目前的名字是誰取的：dm_peer=自動認出、dm_manual=你自己取的 */
  nameSource?: 'dm_peer' | 'dm_manual' | null
}

export interface SpacesResponse {
  count: number
  total: number
  cached: boolean
  cached_at: string | null
  spaces: Space[]
}

/** `GET /api/v1/messages` 的一則訊息。 */
export interface ChatMessage {
  name: string
  sender: string
  sender_id: string | null
  /** 已經是 `YYYY-MM-DD HH:mm` 格式（後端切好的，與送進模型的時間戳一致） */
  time: string
  text: string
  /**
   * 附件的人話描述，例如 `[圖片：shot.png（AI 未讀取內容）]`。
   * 只有圖沒有文字的訊息 `text` 會是空字串、靠這欄才看得出有東西。
   */
  attachment_note?: string
  /** 這則屬於哪一串。私訊幾乎每則各自一串，群組才看得出結構。 */
  thread_name?: string | null
}

export interface MessagesResponse {
  space_id: string
  space_name: string
  /** 有值代表這次回的是「整個討論串」而不是最近 N 則 */
  thread_name?: string | null
  count: number
  messages: ChatMessage[]
}

export type SummaryStyleValue = 'general' | 'technical' | 'action_only'

export interface SummaryStyle {
  value: SummaryStyleValue
  label: string
}

export interface StylesResponse {
  styles: SummaryStyle[]
}

export interface SummaryRecord {
  id: number
  space_id: string
  space_name: string
  style: SummaryStyleValue
  message_count: number
  content_md: string
  created_at: string
}

export interface SummariesResponse {
  count: number
  summaries: SummaryRecord[]
}

export interface PublishResponse {
  status: string
  message_id: string
  createTime: string
  thread_name: string | null
}

/** 使用者可以手動切換的狀態（收件匣的兩個分頁） */
export type MentionState = 'pending' | 'resolved'

/**
 * 資料庫裡實際會出現的狀態。
 *
 * `manual` 是「從摘要工作台按『產生回覆草稿』挑的對話」——它不是有人 @ 你，
 * 所以收件匣刻意不列出來，但草稿工作區要畫得出來。送出回話之後會轉成
 * `resolved`，那時就會出現在「已處理」清單裡。
 */
export type MentionStateValue = MentionState | 'manual'

export interface Mention {
  id: number
  space_id: string
  space_name: string
  message_name: string
  thread_name: string | null
  sender_display: string
  create_time: string
  state: MentionStateValue
  resolved_at: string | null
  text?: string | null
  content_error?: string | null
}

export interface MentionsResponse {
  count: number
  counts: MentionCounts
  mentions: Mention[]
}

export interface MentionRefreshResponse {
  ran: boolean
  stats: CollectorRunStats
}

export interface ReplyResponse {
  status: string
  message_id: string
  createTime: string
  /** 主要那則（單則送出時的既有欄位） */
  mention: Mention
  /** 這次實際結掉的全部（合併回覆時 > 1 則） */
  mentions?: Mention[]
}

export interface UsageRow {
  day: string
  model: string
  prompt_tokens: number
  output_tokens: number
  total_tokens: number
  calls: number
}

export interface UsageResponse {
  usage: UsageRow[]
}

export interface DraftReferenceSpace {
  space_id: string
  space_name: string
  message_count: number
}

/** 合併回覆時，這份草稿實際會回掉的其中一則。 */
export interface DraftAnsweringItem {
  mention_id: number
  sender_display?: string | null
  create_time?: string | null
}

/** Draft Reply 的脈絡形狀（後端 `core/draft_context.py` 的 `DraftContext.to_meta()`）。 */
export interface DraftContextMeta {
  /**
   * `flat_window`：私訊／不分串聊天室，取錨點前後窗
   * `thread`：群組長討論串，維持原本的「整串」
   * `thread_thin`：群組薄串（有人 @ 你但還沒人回），原串 + 帶警語的跨串小窗
   */
  mode: 'flat_window' | 'thread' | 'thread_thin'
  message_count: number
  /** 這次要回覆的訊息有幾則（收件匣多選合併時 > 1） */
  anchor_count?: number
  /** `partial` 代表系統沒能取回錨點周圍的完整對話（那則太舊了） */
  coverage: 'full' | 'partial'
  time_range: { start: string; end: string }
  blocks: { kind: string; label: string; count: number }[]
}

/** SSE `meta` 事件：摘要與 Draft Reply 共用一個型別，欄位各自可選。 */
export interface SseMeta {
  type: 'meta'
  space?: string
  space_id?: string
  message_count?: number
  style?: SummaryStyleValue
  mention_id?: number
  thread_message_count?: number
  /**
   * 這次實際送進模型的脈絡是什麼形狀。**這是使用者判斷草稿可不可信的唯一依據**：
   * 私訊與不分串聊天室走「錨點前後窗」，群組走「同一討論串」，兩者的涵蓋範圍
   * 差很多，而從草稿內容完全看不出來是哪一種。
   */
  context?: DraftContextMeta
  /**
   * 這份草稿會回掉哪幾則 Mention（合併回覆）。送出時要照這份走，
   * 不要沿用送出前的勾選——伺服器實際採用的才算數。
   */
  answering?: DraftAnsweringItem[]
  reference_spaces?: DraftReferenceSpace[]
  /**
   * 實際送進模型的圖片張數。後端一直有送這個欄位，但前端沒顯示，
   * 於是使用者看不出附件到底有沒有被讀——只能從草稿內容有沒有提到圖片來猜。
   */
  image_count?: number
  /** 有附件但沒送進去的原因（格式不支援、超過大小上限…） */
  images_skipped?: string[]
  /**
   * 伺服器實際採用的供應商與模型。要顯示「這份結果是誰產的」一律以這兩個欄位為準——
   * 送出前選的可能是別名，伺服器解析後用的未必是同一個。
   */
  provider?: string
  model?: string
}

export interface SseChunk {
  type: 'chunk'
  text: string
}

export interface SseDone {
  type: 'done'
  summary_id?: number
  draft_id?: number
}

export interface SseError {
  type: 'error'
  code: string
  message: string
}

export type SseEvent = SseMeta | SseChunk | SseDone | SseError
