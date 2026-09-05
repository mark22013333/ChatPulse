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
  lastActiveTime: string | null
  memberCount?: number | null
  pinned?: boolean
}

export interface SpacesResponse {
  count: number
  total: number
  cached: boolean
  cached_at: string | null
  spaces: Space[]
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

export type MentionState = 'pending' | 'resolved'

export interface Mention {
  id: number
  space_id: string
  space_name: string
  message_name: string
  thread_name: string | null
  sender_display: string
  create_time: string
  state: MentionState
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
  mention: Mention
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

/** SSE `meta` 事件：摘要與 Draft Reply 共用一個型別，欄位各自可選。 */
export interface SseMeta {
  type: 'meta'
  space?: string
  space_id?: string
  message_count?: number
  style?: SummaryStyleValue
  mention_id?: number
  thread_message_count?: number
  reference_spaces?: DraftReferenceSpace[]
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
