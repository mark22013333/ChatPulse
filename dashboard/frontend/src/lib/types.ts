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
  /**
   * 每個供應商各記一個 model 裸值：`{provider實作名: 裸值}`。沒設過的不出現。
   *
   * 為什麼是一個物件而不是單一字串：model 的意義依附在 provider 上，`opus`
   * 對 Gemini 沒有意義。存單一字串的話，Viewer 一換供應商偏好就變成非法值。
   */
  default_models?: Record<string, string>
  /** 草稿頁預選的參考專案與環境（ADR-0006） */
  default_code_project_id?: number | null
  default_code_environment?: CodeEnvironment | null
  /**
   * Draft Reply 的回覆設定預設值（ADR-0007）。
   *
   * `default_style` 是**摘要**的章節結構，`default_reply_tone` 是**回話**的
   * 語氣——兩者不同層次也不同值域，不要混用。
   *
   * 這四個欄位在 `PATCH /api/v1/preferences` 上的語意與 `default_provider`
   * **不同**：送 `null` 代表「清除」，不是「不改」。「不使用 Persona」是
   * 使用者會主動選的狀態，必須存得下去。
   */
  default_reply_tone?: string | null
  default_persona_id?: number | null
  default_reply_prompt_id?: number | null
  default_sepia_enabled?: boolean | null
}

/**
 * 一個回覆口氣（`GET /api/v1/reply-tones`）。
 *
 * 刻意**沒有** `instruction` 欄位——那是送給模型的 prompt 片段，前端不需要。
 * `example` 是固定的預覽文字：所有 tone 的 example 都在描述同一個事實
 * （「production 的 timeout 是 30 秒」），並排顯示時使用者一眼就看得出
 * **變的是語氣、不是內容**，而且不必為了預覽去打一次 AI。
 */
export interface ReplyTone {
  id: string
  label: string
  description: string
  example: string
}

/** `GET /api/v1/reply-tones` 的回應。 */
export interface ReplyToneConfig {
  tones: ReplyTone[]
  default: string
}

/**
 * 已淨化的 Persona profile。
 *
 * 這是 Persona 唯一會進 prompt 的形狀——遠端原文永遠不進（見 ADR-0007）。
 * 每個欄位都是短句清單，因為淨化管線刻意把 Persona 能表達的東西限制在
 * 「一組風格形容詞」，讓它在語意上就沒有空間表達指令。
 */
export interface PersonaProfile {
  name: string
  description?: string
  thinking_style: string[]
  communication_style: string[]
  response_preferences: {
    verbosity?: 'low' | 'medium' | 'high'
    prefer_examples?: boolean
    prefer_concrete_language?: boolean
  }
  avoid: string[]
  boundaries?: string[]
  schema_version?: number
}

/**
 * 一個匯入的 Persona（`GET /api/v1/personas`）。
 *
 * `source_commit_sha` 不是稽核裝飾：沒有它就無法保證「今天產生的草稿
 * 明天還是同樣行為」，因為遠端隨時可以改 SKILL.md。UI 要把它顯示出來。
 */
export interface Persona {
  id: number
  name: string
  description: string
  source_type: 'github' | 'url' | 'manual'
  source_repository?: string | null
  source_url?: string | null
  source_ref?: string | null
  source_commit_sha?: string | null
  source_hash?: string | null
  enabled: boolean
  imported_at: string
  refreshed_at?: string | null
  created_at: string
  updated_at: string
  profile: PersonaProfile
  /** 只在 `?include_raw=true` 時出現，僅供 debug——**不得拿去組 prompt**。 */
  raw_source?: string
}

/** Persona 來源型別（`GET /api/v1/personas` 的 `sources`）。 */
export interface PersonaSourceInfo {
  name: string
  label: string
}

/** 存起來重複使用的自訂提示詞（`GET /api/v1/reply-prompts`）。 */
export interface ReplyPrompt {
  id: number
  name: string
  description: string
  prompt: string
  created_at: string
  updated_at: string
}

/** 一個潤稿器與它現在可不可用（`GET /api/v1/polishers`）。 */
export interface PolisherInfo {
  name: string
  label: string
  available: boolean
  reason: string
}

/** vendored 的 Sepia 規則版本資訊（`GET /api/v1/polishers` 的 `sepia`）。 */
export interface SepiaRulesInfo {
  name?: string
  version?: string
  source_repository?: string
  source_ref?: string
  source_commit_sha?: string
  license?: string
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
  /**
   * 目前生效的**裸值**（`opus`、`gemini-3.6-flash`），送 request 時帶的就是它。
   *
   * ⚠️ 與上面的 `model` **不是**同一種字串：`model` 是記進用量的識別字，
   * ClaudeCLI 會加前綴變成 `claude-cli:opus`。把識別字拿去當 model 參數送，
   * 後端會再加一次前綴變成 `claude-cli:claude-cli:opus`。要顯示用量時用
   * `model`，要當參數送出時用 `model_id`。
   */
  model_id: string
  /**
   * 這個供應商的常用裸值清單。**不是完整列舉**——後端只放驗證過的值，
   * 沒列到的由 Viewer 自己打字輸入，所以不可以拿它來擋 Viewer 的輸入。
   */
  models: string[]
}

/** `GET /api/v1/providers` 的回應，也是 `/me` 的 `ai` 欄位內容。 */
export interface AIConfig {
  /**
   * 伺服器預設。可能是別名（`claude`／`auto`，會挑第一個可用的實作），
   * 也可能直接是實作名稱之一——所以不保證對得上 `providers` 裡的任何 `name`。
   */
  default: string
  providers: AIProvider[]
  /**
   * Viewer 存過的 model 偏好：`{provider實作名: 裸值}`，沒設過的 provider 不出現。
   *
   * 選填是因為契約只保證 `/me` 的 `ai` 帶這個鍵；未登入也能打的 `/providers`
   * 沒有 Viewer 可言，自然不會有偏好。讀的時候一律當它可能不存在。
   */
  default_models?: Record<string, string>
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
  /**
   * 目前這位 Viewer 最近一次對這個 Space 產生 Summary 的時間（DB 的 created_at，
   * UTC、`+00:00` 結尾）；沒產生過是 null。只算自己的（ADR-0002）。
   * 狀態判定見 `lib/spaceTable.ts` 的 `summaryStatusOf`。可選：舊後端與測試
   * fixture 沒有這個欄位，缺少時一律當成「無法判斷」，不當成「從未產生」。
   */
  lastSummaryAt?: string | null
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
  /**
   * 這一則有沒有**存下來**的草稿。
   *
   * 供清單提示；開啟 Mention 時由草稿端點確認是否有可還原內容。
   */
  has_draft?: boolean
}

/**
 * 從資料庫讀回來的既有草稿（`GET /mentions/{id}/draft`）。
 *
 * `generation_config` 有**兩種形狀**，讀的時候要都吃得下：
 *
 * * **2026-09-11 之後**：多一個 `meta`，就是產生當下送給瀏覽器的那份
 *   SSE meta 原件。證據欄可以完整還原。
 * * **更早**（本機 53 筆）：只有平鋪的 `{provider, model} ＋ 回覆設定 ＋
 *   潤稿結果`，也就是證據欄的「生成」「回話設定」「潤稿」三列。脈絡、
 *   參考 Space、程式碼佐證、合併回覆對象當時沒有存，**補不回來**。
 */
export interface StoredDraft {
  draft_id: number
  mention_id: number
  content_md: string
  generation_config: Partial<DraftReplySettingsMeta> & {
    provider?: string
    model?: string
    polished?: boolean
    polisher?: string
    polish_model?: string
    fallback_reason?: string
    /** 完整的產生當下 meta（新版才有）。 */
    meta?: SseMeta
  }
  created_at: string
  sent_at: string | null
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

/** 參考專案的環境。與後端 cfg.CODE_ENVIRONMENTS 是同一組封閉字彙。 */
export type CodeEnvironment = 'production' | 'uat' | 'dev'

export interface CodeProjectBranchInfo {
  branch: string
  exists: boolean
  commit?: string
  commit_date?: string
  error?: string
  did_you_mean?: string[]
}

export interface CodeProjectVerification {
  repo_ok: boolean
  branches: Record<string, CodeProjectBranchInfo>
  working_tree_dirty: boolean
  error?: string | null
}

export interface CodeProject {
  id: number
  name: string
  repo_path: string
  default_env: CodeEnvironment
  include_globs: string[]
  exclude_globs: string[]
  enabled: boolean
  /** 環境 → 分支。這是整個功能的重點：查問題時不能查錯環境。 */
  branches: Partial<Record<CodeEnvironment, string>>
  last_verified_at?: string | null
  last_verify_error?: string | null
  verification?: CodeProjectVerification
}

/**
 * 「我的工作 repo」的驗證結果（ADR-0008、api-contract「Draft Worklog」節）。
 *
 * **形狀刻意與 `CodeProjectVerification` 不同**：那邊要驗「每個環境釘死的分支
 * 還在不在」，這邊沒有釘死的分支可以驗，硬套同一個形狀只會多出一個永遠空著
 * 的 `branches` 欄位。
 */
export interface WorkRepoVerification {
  ok: boolean
  /** `ok: false` 時是可以直接顯示的繁中句子，同一份字串也會寫進 `last_verify_error`。 */
  error?: string | null
  /** 剛 `git init`、還沒有任何 commit 的 repo 是 `null`。那**不是**錯誤，`ok` 仍然是 `true`。 */
  head_sha?: string | null
  /**
   * 最近 200 筆 commit 的作者信箱，去重後最近用過的排前面。
   *
   * **它是給人看的參考，後端不會自動寫進 `author_emails`**——裡面本來就會混進
   * 同事的信箱。要由人挑，系統不自動發現。
   */
  detected_emails?: string[]
}

/**
 * 「我的工作 repo」：工時草稿要從哪些本機 git repo 撈 commit 當證據。
 *
 * **與 `CodeProject`（參考專案）是兩份不同的登錄，不要共用。** 那邊是為了
 * 「查程式碼回答問題」而登的，必須釘死是哪個環境的哪一版；這邊是為了
 * 「回想這天做了什麼」。兩者的分支語意正好相反，見 `branch_filter`。
 */
export interface WorkRepo {
  id: number
  name: string
  /** 絕對路徑。相對路徑會相對於伺服器的工作目錄去解，那跟使用者心裡想的不是同一個地方。 */
  repo_path: string
  /**
   * 要撈誰的 commit。**陣列不是單值**：同一個人在不同 repo 常常設不同的
   * `user.email`，只填一個會讓另一個信箱的 commit 靜默漏掉。
   *
   * ⚠️ **空陣列＝不限作者**，會撈到這個 repo 裡所有人的 commit。後端不擋。
   */
  author_emails: string[]
  /**
   * 分支白名單。⚠️ **空陣列＝掃全部分支（`--all`）**，不是「不掃」。
   *
   * 這與 `CodeProject.branches`「環境 → 釘死一個分支」**相反**，不是疏忽：
   * 那邊拿錯環境等於給出錯的答案，這邊漏掉一個還沒合併的 feature 分支
   * 就等於漏掉那天的工作。
   */
  branch_filter: string[]
  /**
   * ⚠️ **只用來把該專案的 issue 排進候選清單並標示來源，絕不自動填工時**
   * （ADR-0008 的唯一硬規則：專案歸屬一律由 Viewer 在確認畫面選）。
   */
  default_zplanner_project_id?: number | null
  default_zplanner_project_key?: string | null
  enabled: boolean
  last_verified_at?: string | null
  last_verify_error?: string | null
  created_at?: string
  updated_at?: string
  /** POST／PATCH／verify 會回帶；GET 清單不會。 */
  verification?: WorkRepoVerification
}

/**
 * `meta` 事件裡的檢索結果：模型開口**之前**就送到前端。
 * 讓 Viewer 一眼判斷依據對不對（搜錯環境、搜錯關鍵字），
 * 不必先讀完一整段生成文字。
 */
export interface DraftCodeRef {
  project_name: string
  environment: CodeEnvironment
  environment_label: string
  branch: string
  commit_sha: string
  commit_date: string
  terms: string[]
  hit_count: number
  files: string[]
  truncated: boolean
  notes: string[]
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
  code_refs?: DraftCodeRef[]
  code_skipped?: string[]
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
  /**
   * 這次實際套用的回覆設定（ADR-0007）。與 `code_refs` 同一個理由：
   * 讓使用者在模型開口**之前**就看到「系統以為我選了什麼」。
   *
   * 只有被套用的項目才會出現。`custom_prompt` 刻意只有布林——
   * 自訂提示的全文是使用者輸入，沒有必要回送到瀏覽器。
   */
  reply?: DraftReplySettingsMeta
}

/** SSE `meta` 事件裡的回覆設定摘要。 */
export interface DraftReplySettingsMeta {
  tone?: string
  tone_label?: string
  persona_id?: number
  persona_name?: string
  /** 有沒有套用自訂提示（不含內容本身） */
  custom_prompt?: boolean
  custom_prompt_id?: number
  sepia: boolean
}

/**
 * 潤稿的結果（SSE `done` 事件的 `polish`，以及草稿的 generation config）。
 *
 * `polished: false` 有三種原因，`fallback_reason` 會說是哪一種：
 * 完整性檢查沒過、模型沒照輸出契約回、或草稿裡找不到〈建議回話〉章節。
 * **這與「Sepia 根本不可用」是不同的事**——後者會讓請求收到
 * `SEPIA_UNAVAILABLE` 錯誤，根本不會走到這裡。
 */
export interface DraftPolishMeta {
  polisher: string
  polished: boolean
  fallback_reason?: string
  polish_model?: string
}

export interface SseChunk {
  type: 'chunk'
  text: string
}

export interface SseDone {
  type: 'done'
  summary_id?: number
  draft_id?: number
  /**
   * 潤稿後的〈建議回話〉全文（Draft Reply 專用，未潤稿時為 null）。
   *
   * **這不是 UX 裝飾。** 潤稿發生在串流結束之後，所以前端累積的 `raw` 是
   * 未潤稿的版本，而資料庫存的是潤稿後的版本。使用者按「送出」時送的是
   * 前端這一份——沒有這個欄位，開了 Sepia 就會把未潤稿的內容送到
   * Google Chat，而且畫面上看不出差別。
   */
  reply?: string | null
  /** 潤稿 meta（未啟用時為 null） */
  polish?: DraftPolishMeta | null
}

export interface SseError {
  type: 'error'
  code: string
  message: string
  /**
   * 錯誤主體的 `error.detail`（結構化補充，只有拋的人刻意給時才有）。
   *
   * ⚠️ 只有**串流開始之前**就被擋下的 4xx 才帶得到它（例如重產守門的
   * `assigned_count`）；串流中途才發生的錯誤走後端的 `to_sse_event`，
   * 不帶 detail。所以讀它時一律要處理「不存在」——不要從 `message` 的
   * 中文句子裡用正則挖數字，文案改一個字就會安靜地挖錯。
   */
  detail?: Record<string, unknown> | null
}

export type SseEvent = SseMeta | SseChunk | SseDone | SseError

/**
 * `GET /api/v1/health` 的回應（`dashboard/api/server.py` 的 `health`）。
 *
 * 這支端點不需要登入——診斷頁排在登入 gate 之前，因為「後端起來了嗎、
 * AI 供應商設好了嗎」正是還沒登入時最需要問的事。
 */
export interface HealthResponse {
  status: string
  /** SQLite 的 journal mode，正常是 `wal` */
  db: string
  ai_provider_default: string
  ai_provider_active: string
  gemini_configured: boolean
  collector_running: boolean
  /** 採集器實作（ADR-0004 目前是 `polling`） */
  collector_implementation: string
  viewer_count: number
}

/**
 * 一份工時草稿的狀態。**只有這三個**（`core/db.py` 的 `worklog_drafts.status`）。
 *
 * `partial` 必須是獨立狀態，不可以併進 `draft`：它是「有幾筆送成功、有幾筆
 * 沒有」，當成 `draft` 會讓人以為整天都還沒送，再按一次就把已經成功的那幾筆
 * 重複填進 ZPlanner。
 */
export type WorklogDraftStatus = 'draft' | 'partial' | 'submitted'

/**
 * `GET /api/v1/worklogs/week` 裡某一天的**本地草稿**摘要（不含 entries 本體）。
 *
 * ⚠️ **時數欄位一律是字串**（`"8.00"`），不是 number：DB 存 TEXT、ZPlanner 的
 * 寫入端點吃的也是字串，後端一路沒有轉成數字（`core/repository.py` 的
 * `_hours_sum()` 回 `f"{total:.2f}"`）。要算術就自己 `Number.parseFloat()`，
 * 而且要處理算不動的情況——`_hours_sum()` 對壞值的策略是**跳過但不失敗**，
 * 所以壞值不會讓端點噴錯，只會讓某個數字安靜地變小。
 *
 * ⚠️ **不要照 ZPlanner 原生的形狀寫型別。** ZPlanner 自己的
 * `GET /api/worklogs/weekly/` 是 `days[].hours` float、`weekday` 英文縮寫
 * （見 `core/zplanner_client.py` 的 `list_weekly_worklogs` docstring）；
 * ChatPulse 這一層**已經全部正規化過**，兩者不是同一個形狀。
 */
export interface WorklogWeekDraft {
  id: number
  work_date: string
  status: WorklogDraftStatus
  /**
   * Viewer 輸入的「這天總共幾小時」。**與 `hours` 不保證相等**——湊成 0.5
   * 倍數必然有餘數，真正會送進 ZPlanner 的是 `hours`（各筆之和）。
   */
  total_hours: string | null
  /** 切日界用的時區偏移（`+08:00`）。「半夜一點的 commit 算哪天」由它決定。 */
  tz_offset: string | null
  /** 產生當下的 SSE meta 快照＝證據欄的唯一來源。週欄用不到，留著讓型別完整。 */
  generation_meta?: Record<string, unknown> | null
  generated_at?: string | null
  created_at?: string
  updated_at?: string
  entry_count: number
  /** 各筆之和。**真正會送出的是這個數字。** */
  hours: string
  /** 系統原始分配的總和。與 `hours` 分開存才看得出人改過什麼。 */
  allocated_hours: string
  submitted_count: number
  failed_count: number
  /**
   * 送出時逾時的筆數。**`unknown` 不是 `failed`**：逾時不保證沒寫進去，
   * 這種筆一律不自動重試（計畫 §5）。
   */
  unknown_count: number
  /** 還沒選專案的筆數。**未選擋住送出**（ADR-0008），週欄要看得出還差幾筆。 */
  unassigned_count: number
}

/** 某一天**已經填進 ZPlanner** 的工時（不是本地草稿）。ZPlanner 不可用時是 `null`。 */
export interface WorklogWeekZplannerDay {
  /** 字串，理由同 `WorklogWeekDraft.hours`。 */
  hours: string
  worklog_count: number
}

export interface WorklogWeekDay {
  work_date: string
  /**
   * **ISO 星期（1＝週一、7＝週日）的數字**，不是英文縮寫。
   *
   * 後端刻意回這個而不是讓前端從日期字串反推——反推會在跨時區時錯一天。
   * 轉成中文要用**明確的對照表**查這個值，不可以拿它當陣列索引：後端哪天
   * 改了起算基準，索引會安靜地整排錯位，而對照表查不到就是查不到。
   */
  weekday: number
  draft: WorklogWeekDraft | null
  zplanner: WorklogWeekZplannerDay | null
}

export interface WorklogWeekTotals {
  /** 本週所有草稿的 `hours` 之和（字串）。 */
  draft_hours: string
  days_with_draft: number
  days_submitted: number
  entries_unassigned: number
}

/**
 * 週檢視裡 ZPlanner 那一段。
 *
 * 🔴 **`available: false` 不是錯誤，本地草稿照常回。** 沒設 ZPlanner 的人仍然
 * 看得到自己的草稿，所以前端**不可以**把它當成載入失敗、也不可以靜默隱藏
 * ——`reason` 是可直接顯示的繁中句子，要寫在畫面上。
 */
export type WorklogWeekZplanner =
  | {
      available: true
      /** 本週已填進 ZPlanner 的總時數（字串）。 */
      total_hours: string
      /** **工時筆數**，不是有填工時的天數。 */
      worklog_count: number
      /**
       * 本週的目標時數（字串）與完成率（float），**由 ZPlanner 算**。
       *
       * ⚠️ **取不到時這兩個欄位整個不存在**（不是 `null`）——後端刻意不補
       * 預設值，因為編一個 40 小時的目標會讓完成率變成看起來精確的假數字。
       * 所以前端要用「有沒有這個欄位」決定畫不畫，不要寫 `?? 40`。
       *
       * 目標會因人、因請假而不同，所以**不要自己算完成率**；「還差幾小時」
       * 是單純的減法（兩個數都是 ZPlanner 給的），那個可以自己算。
       */
      target_hours?: string
      completion_pct?: number
    }
  | { available: false; reason: string }

/** `GET /api/v1/worklogs/week` 的回應。 */
export interface WorklogWeekResponse {
  /**
   * ⚠️ **要顯示「這是哪一週」一律讀這兩個欄位，不要拿自己送出去的參數當答案。**
   *
   * ZPlanner 會把日期正規化到那一週的週一（2026-09-13 實測：傳週二回的是
   * 週一），而週的起點是顯示決定、不是後端決定。讀回應永遠對得起來。
   */
  week_start: string
  week_end: string
  /** **七天一律回滿**，沒有草稿的那天 `draft` 是 `null`。前端不必自己補空格。 */
  days: WorklogWeekDay[]
  totals: WorklogWeekTotals
  zplanner: WorklogWeekZplanner
}

/**
 * 草稿裡一筆工時的送出狀態。**只有這五個**（`core/db.py` 的
 * `worklog_draft_entries.state`）。
 *
 * ⚠️ `unknown` 不是 `failed`：「不知道送進去沒有」（傳輸層斷線、或 ZPlanner
 * 回成功卻沒給 worklog id）。畫成失敗會讓人按重送，ZPlanner 那側就可能多一筆
 * 月結後刪不掉的重複工時。
 */
export type WorklogEntryState = 'pending' | 'skipped' | 'submitted' | 'failed' | 'unknown'

/**
 * 一個候選 issue（`generation_meta.clusters[].candidates[]`，也存進每筆 entry）。
 *
 * 🔴 **候選不是歸屬。** 系統只給候選與理由，選不選、選哪個由 Viewer 決定
 * （ADR-0008）。`confidence` 只有三級並一律附 `reason`——要讓人讀理由，
 * 不是讀名次，所以前端不做排名徽章或百分比。
 */
export interface WorklogCandidate {
  issue_id: number
  issue_key: string
  title: string
  project_id: number | null
  project_key: string | null
  source: string
  confidence: 'strong' | 'medium' | 'weak'
  reason: string
  /** 查不到權限時是 `null`（不假裝有權限） */
  can_worklog: boolean | null
  permission_checked: boolean
  permission_reason: string
  /**
   * 只有 `source === 'last_used'`（「這個來源上次記到 XXX」）才有：那次記憶的時間。
   * 🔴 那一項同樣**只是候選**，四個歸屬欄位不會因此有值——要 Viewer 點了才算。
   */
  last_used_at?: string | null
  /**
   * 只有 `last_used` 那一項才有：同一張單這次也被系統搜到時，搜到的來源
   * （`issue_key`／`default_project`／`search`）。有值時 `confidence`／`reason` 已併入
   * 搜到那一邊（強訊號不被記憶的「中」降級），也代表這一段**有**系統命中。
   */
  matched_source?: string | null
}

/**
 * `GET`／`PUT /api/v1/worklogs/{date}` 回應裡的一筆（contract §1）。
 *
 * 形狀＝repository 的 `_worklog_entry_row` 全部鍵，再加上 server 依
 * `evidence_refs` 集合相等對回 `generation_meta.clusters[]` 取得的
 * `cluster_key`／`label`／`reason`。
 *
 * ⚠️ **這三個補上的鍵可能是 `null`**：重產時 meta 與 entries 分兩個交易寫，
 * 後者被拒時 meta 已經換新，對不到就給 null（D-9），刻意不退回用位置對——
 * 用位置對會把新群的理由安靜地貼到舊的那筆上。畫面要能畫「沒有分群理由」。
 *
 * ⚠️ 時數欄位一律是字串，理由同 `WorklogWeekDraft.hours`。
 */
export interface WorklogDayEntry {
  id: number
  draft_id: number
  seq: number
  cluster_key: string | null
  label: string | null
  reason: string | null
  /**
   * 🔴 **產生時一律是 `null`**（ADR-0008 的唯一硬規則）。有值代表 Viewer
   * 自己選過了。送出閘看的是 `issue_id`，重產守門看 `issue_id or issue_key`，
   * 所以四個歸屬欄位一律一起寫（D-12），不要只寫一半。
   */
  issue_id: number | null
  issue_key: string | null
  project_id: number | null
  project_key: string | null
  /** 實際會送出的時數（人改過的值）。還沒輸入總時數時整份都是 `null`，那是正常狀態。 */
  hours: string | null
  note: string | null
  /** 系統原始分配。與 `hours` 分開存才看得出人改過什麼。 */
  allocated_hours: string | null
  weight: number | null
  /** 有沒有拿到湊整的那 0.5 小時 */
  rounded_up: boolean
  evidence_refs: string[]
  candidates: WorklogCandidate[]
  state: WorklogEntryState
  /** 有值 ＝ 已經送進 ZPlanner。這是「這筆送過了」的唯一憑據，有值的筆 PUT 會 409（D-21）。 */
  zplanner_worklog_id: number | null
  zplanner_issue_key: string | null
  is_locked: boolean
  submitted_at: string | null
  last_error_code: string | null
  last_error_message: string | null
  created_at: string
  updated_at: string
}

/**
 * `GET`／`PUT /api/v1/worklogs/{date}` 的回應（contract §1）：**扁平的草稿物件**，
 * 不包外層（比照 submit 回應）。
 *
 * 這是前端 entries 的**唯一來源**（D-10）：PUT 用回應整份取代、done 與 submit
 * 之後各重讀一次 GET。三種回應各寫一個 mapper 遲早會漂移。
 */
export interface WorklogDay {
  id: number
  work_date: string
  status: WorklogDraftStatus
  total_hours: string | null
  tz_offset: string | null
  generation_meta: Record<string, unknown> | null
  generated_at: string | null
  created_at: string
  updated_at: string
  entries: WorklogDayEntry[]
}

/**
 * `PUT /api/v1/worklogs/{date}` 的單筆（contract §2）。
 *
 * ⚠️ **鍵沒帶＝不改，帶 `null`＝清除**——兩者語意不同，組 body 時不可以用
 * `?? null` 把「沒帶」補成 `null`，那會把 Viewer 沒碰的欄位清掉。
 * 歸屬四欄任一有帶就四個都要帶，否則 400（D-12）。
 */
export interface WorklogEntryUpdate {
  id: number
  hours?: string | null
  note?: string | null
  issue_id?: number | null
  issue_key?: string | null
  project_id?: number | null
  project_key?: string | null
}

/** submit 回應 `summary.skipped_duplicates[]` 的一筆。 */
export interface WorklogSkippedDuplicate {
  entry_id: number
  seq: number
  issue_key: string | null
  hours: string | null
  /** 比對到的那一列遠端工時原文 */
  existing: Record<string, unknown>
  /** 可直接顯示的繁中句子 */
  reason: string
}

/**
 * submit 回應的 `summary`。
 *
 * 🔴 四個計數描述的是「重讀之後整份草稿」，不是「這一次送了幾筆」。
 * `not_attempted_count` 一律都在（即使是 0），> 0 代表整批時間預算用完、
 * 剩下的筆沒開始送——在 DB 裡它們與「從未送過」長得一樣，這個數字是
 * Viewer 唯一看得出「這批沒跑完」的訊號。
 */
export interface WorklogSubmitSummary {
  submitted_count: number
  skipped_count: number
  failed_count: number
  unknown_count: number
  skipped_duplicates: WorklogSkippedDuplicate[]
  /** 工時已寫進 ZPlanner、卻寫不回草稿的筆（帶 `zplanner_worklog_id`）。正常是空的。 */
  orphans: Record<string, unknown>[]
  not_attempted_count: number
}

/**
 * `POST /api/v1/worklogs/{date}/submit` 的回應。
 *
 * 🔴 **HTTP 200 不代表每一筆都成功**，逐筆看 `entries[].state`。
 * `entries[]` 沒有 `cluster_key`／`label`／`reason`，所以前端不拿它重畫，
 * 而是再讀一次 GET（D-10），這裡只留 `summary` 當暫態。
 */
export interface WorklogSubmitResponse {
  work_date: string
  status: WorklogDraftStatus
  entries: Omit<WorklogDayEntry, 'cluster_key' | 'label' | 'reason'>[]
  summary: WorklogSubmitSummary
}

/** submit 的 body。省略 `entry_ids` ＝ 送整天；空陣列會 400。 */
export type WorklogSubmitBody = { entry_ids?: number[] }

/** draft/stream `meta` 裡的一群（只列前端用得到的欄位）。 */
export interface WorklogSseCluster {
  key: string
  kind: string
  label: string
  reason: string
  weight: number
  evidence_refs: string[]
  candidates: WorklogCandidate[]
}

/**
 * `POST /worklogs/{date}/draft/stream` 的 `meta` 事件（只列前端用得到的欄位）。
 *
 * 刻意另開型別、不併進 `SseMeta`（D-17）：兩者形狀完全不同，聯集進去會讓
 * Draft Reply 那邊的每個欄位都變成「可能是工時的東西」。
 */
export interface WorklogSseMeta {
  type: 'meta'
  work_date: string
  tz_offset: string
  commits: Record<string, unknown>[]
  chat: Record<string, unknown>[]
  clusters: WorklogSseCluster[]
  zplanner: { available: boolean; reason: string } & Record<string, unknown>
  provider: string | null
  model: string | null
  note_source: string
}

/**
 * draft/stream 的 `chunk`：每群一個。
 *
 * ⚠️ 這一階段的 `text` 不是 AI 寫的，是該群前兩筆 commit subject 相接。
 */
export interface WorklogSseChunk {
  type: 'chunk'
  index: number
  cluster_key: string
  text: string
}

/** done 事件的 `allocation`（`AllocationResult.to_dict()`）。省略總時數時整個是 `null`。 */
export interface WorklogAllocation {
  total_hours: string
  entries: {
    index: number
    /** ⚠️ 分配演算法的 tie-break 依據，**不是歸屬**，前端不可拿它去預選。 */
    issue_key: string | null
    allocated_hours: string
    rounded_up: boolean
    merged_into: number | null
  }[]
  adjustments: Record<string, unknown>[]
}

/**
 * draft/stream 的 `done`。
 *
 * `entries` 刻意不列進型別：前端 entries 的唯一來源是 GET（D-10），收到
 * done 之後重讀一次，不從這裡組卡片——少一個 mapper 就少一條會漂移的路。
 */
export interface WorklogSseDone {
  type: 'done'
  draft_id: number
  allocation: WorklogAllocation | null
  /**
   * 重產時因分段改變而清空的歸屬（2026-10-01 起）。`count` 是段數，`issue_keys` 是被清掉的
   * 單號（去重、沒有單號的不列）。舊後端沒有這個鍵，所以是選填。
   */
  attributions_cleared?: { count: number; issue_keys: string[] }
}

/**
 * `GET /api/v1/worklogs/issue-search?q=` 的一筆：與 `WorklogCandidate` 的前五個欄位同形。
 *
 * 搜尋結果**沒有**信心度、理由與權限預檢：worklogable 本身就是「能不能填」的權威
 * 來源，逐專案的角色預檢是產草稿時的事（每專案 2 次 HTTP，打字時跑會慢到不能用）。
 */
export interface WorklogIssueSearchIssue {
  issue_id: number
  issue_key: string
  title: string
  project_id: number | null
  project_key: string | null
}

/** 搜尋結果依專案分組。`project_key` 為 `null` 的組排最後。 */
export interface WorklogIssueSearchGroup {
  project_id: number | null
  project_key: string | null
  issues: WorklogIssueSearchIssue[]
}

/**
 * `GET /api/v1/worklogs/issue-search` 的回應。
 *
 * ⚠️ `q` 不可為空白——後端直接 400，因為 ZPlanner 對空字串回 0 筆而不是全部；
 * 前端也在送出前擋下（`normalizeIssueQuery`），不要依賴 400。
 */
export interface WorklogIssueSearchResponse {
  q: string
  count: number
  groups: WorklogIssueSearchGroup[]
}
