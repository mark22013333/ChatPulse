import type {
  AIConfig,
  AuthResult,
  AuthStatus,
  CodeEnvironment,
  CodeProject,
  Me,
  Preferences,
  Mention,
  MentionRefreshResponse,
  MentionState,
  MentionsResponse,
  MessagesResponse,
  Persona,
  PersonaSourceInfo,
  PolisherInfo,
  PublishResponse,
  ReplyPrompt,
  ReplyResponse,
  ReplyToneConfig,
  SepiaRulesInfo,
  SpacesResponse,
  StoredDraft,
  StylesResponse,
  SummariesResponse,
  SummaryStyleValue,
  HealthResponse,
  UsageResponse,
} from './types'

export const API_BASE = '/api/v1'

/** limit 的合法區間（規格 5.5，與後端 Field(ge=1, le=1000) 對齊）。 */
export const LIMIT_MIN = 1
export const LIMIT_MAX = 1000
export const LIMIT_DEFAULT = 50

/**
 * 後端非串流錯誤一律是 `{"error":{"code":"...","message":"..."}}`，
 * message 本身已是繁體中文，直接顯示給使用者即可。
 */
export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly retryAfter: number | null

  constructor(status: number, code: string, message: string, retryAfter: number | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.retryAfter = retryAfter
  }

  get isUnauthenticated(): boolean {
    return this.status === 401 || this.code === 'NOT_AUTHENTICATED'
  }
}

type Json = Record<string, unknown>

async function toApiError(res: Response): Promise<ApiError> {
  let code = `HTTP_${res.status}`
  let message = `請求失敗（HTTP ${res.status}）`
  try {
    const payload = (await res.json()) as { error?: { code?: string; message?: string } }
    if (payload?.error) {
      code = payload.error.code ?? code
      message = payload.error.message ?? message
    }
  } catch {
    // 回應不是 JSON（例如代理層錯誤頁），沿用預設訊息
  }
  const retryHeader = res.headers.get('Retry-After')
  const retryAfter = retryHeader ? Number.parseInt(retryHeader, 10) : null
  return new ApiError(res.status, code, message, Number.isNaN(retryAfter) ? null : retryAfter)
}

/** 401 時通知外層（auth store 會清空狀態並回登入畫面）。 */
let unauthorizedHandler: (() => void) | null = null
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  options: { skipAuthRedirect?: boolean } = {},
): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, {
      credentials: 'same-origin',
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...(init.headers ?? {}),
      },
    })
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') throw err
    throw new ApiError(0, 'NETWORK_ERROR', '無法連線到伺服器，請確認後端服務是否啟動')
  }

  if (!res.ok) {
    const apiError = await toApiError(res)
    if (apiError.isUnauthenticated && !options.skipAuthRedirect) unauthorizedHandler?.()
    throw apiError
  }

  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

function query(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === '') continue
    search.set(key, String(value))
  }
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

function post<T>(path: string, body?: Json, options?: { skipAuthRedirect?: boolean }): Promise<T> {
  return request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }, options)
}

export const api = {
  // ── 認證 ────────────────────────────────────────────────
  authStatus: () => request<AuthStatus>('/auth/status', {}, { skipAuthRedirect: true }),
  login: () => post<AuthResult>('/auth/login', undefined, { skipAuthRedirect: true }),
  bootstrap: () => post<AuthResult>('/auth/bootstrap', undefined, { skipAuthRedirect: true }),
  logout: () => post<AuthResult>('/auth/logout', undefined, { skipAuthRedirect: true }),
  me: () => request<Me>('/me'),

  // ── AI 供應商 ──────────────────────────────────────────
  /** 未登入也能打；登入後可改用 `/me` 的 `ai` 欄位，少一次往返。 */
  providers: () => request<AIConfig>('/providers', {}, { skipAuthRedirect: true }),
  /**
   * 更新偏好。
   *
   * **兩組欄位的 null 語意不同，不要弄混：**
   * - 舊欄位（`default_provider`／`default_limit`／`default_style`）：
   *   後端把 `null`／省略都當成「不改」。
   * - 回覆設定（`default_reply_tone` 等四個，ADR-0007）：
   *   **送 `null` 代表「清除」**，省略才是「不改」。因為「不使用 Persona」
   *   是使用者會主動選的狀態，必須存得下去。
   */
  updatePreferences: (body: {
    /** 取消最後一個釘選要送 `[]`——送 `null` 是「不改」，會靜默地沒有效果 */
    pinned_space_ids?: string[]
    default_provider?: string | null
    default_limit?: number
    default_style?: string
    default_reply_tone?: string | null
    default_persona_id?: number | null
    default_reply_prompt_id?: number | null
    default_sepia_enabled?: boolean | null
  }) =>
    request<Preferences>('/preferences', { method: 'PATCH', body: JSON.stringify(body) }),

  // ── 回覆設定（ADR-0007）────────────────────────────────
  /** 回覆口氣清單。未登入也能打（靜態選項，理由同 `/styles`）。 */
  replyTones: () => request<ReplyToneConfig>('/reply-tones', {}, { skipAuthRedirect: true }),
  /** 潤稿器與 vendored 規則版本。未登入也能打。 */
  polishers: () =>
    request<{ polishers: PolisherInfo[]; sepia: SepiaRulesInfo }>(
      '/polishers',
      {},
      { skipAuthRedirect: true },
    ),

  personas: () =>
    request<{ personas: Persona[]; sources: PersonaSourceInfo[] }>('/personas'),
  /** `include_raw` 只在要看「淨化掉了什麼」時才開——原文有 20 KB 上下。 */
  persona: (id: number, includeRaw = false) =>
    request<Persona>(`/personas/${id}${query({ include_raw: includeRaw || undefined })}`),
  /** 從公開來源匯入並固定版本。同名視為更新（`created: false`）。 */
  importPersona: (body: {
    source_type: string
    repository?: string
    persona?: string
    url?: string
    ref?: string
    name?: string
    /** `notice` 是「匯進來了，但有件事值得看一眼」——目前只有根目錄那一種。 */
  }) =>
    post<{ persona: Persona; created: boolean; notice?: string | null }>(
      '/personas/import',
      body as unknown as Json,
    ),
  /** 列出某個來源 repo 有哪些 Persona 可以匯入。 */
  personaSourceList: (sourceType: string, repository: string, ref?: string) =>
    request<{ personas: Array<Record<string, unknown>> }>(
      `/personas/sources/${encodeURIComponent(sourceType)}/list${query({ repository, ref })}`,
    ),
  /** 重新從原來的來源取得（會更新 commit SHA）。`changed` 說內容有沒有真的變。 */
  refreshPersona: (id: number) =>
    post<{ persona: Persona; created: boolean; changed: boolean }>(`/personas/${id}/refresh`),
  createPersona: (body: {
    name: string
    description?: string
    raw_text?: string
    profile?: Record<string, unknown>
  }) => post<{ persona: Persona; created: boolean }>('/personas', body as unknown as Json),
  /** 只能改名／簡介／啟用狀態。profile 內容只能由匯入流程產生（那條路徑保證跑過淨化）。 */
  updatePersona: (id: number, body: { name?: string; description?: string; enabled?: boolean }) =>
    request<Persona>(`/personas/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deletePersona: (id: number) =>
    request<{ deleted: boolean }>(`/personas/${id}`, { method: 'DELETE' }),

  replyPrompts: () => request<{ reply_prompts: ReplyPrompt[] }>('/reply-prompts'),
  createReplyPrompt: (body: { name: string; description?: string; prompt: string }) =>
    post<ReplyPrompt>('/reply-prompts', body as unknown as Json),
  updateReplyPrompt: (
    id: number,
    body: { name?: string; description?: string; prompt?: string },
  ) => request<ReplyPrompt>(`/reply-prompts/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteReplyPrompt: (id: number) =>
    request<{ deleted: boolean }>(`/reply-prompts/${id}`, { method: 'DELETE' }),

  /**
   * 對某個 Space「對方最後說的話」建立草稿目標，回傳可以拿去產草稿的 mention。
   * 私訊不會產生 mention（沒人 @ 你），所以後端合成一筆給草稿流程掛。
   */
  createDraftTarget: (body: { space_id: string }) =>
    post<{ mention_id: number; mention: Mention | null }>(
      '/spaces/draft-target',
      body as unknown as Json,
    ),

  /** 給沒有官方名稱的空間（私訊）取別名。`alias` 傳空字串＝清除，回到自動辨識。 */
  setSpaceAlias: (body: { space_id: string; alias: string }) =>
    request<{ space_id: string; alias: string; ok: boolean }>('/spaces/alias', {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  // ── Phase 1 ────────────────────────────────────────────
  spaces: (params: { search?: string; refresh?: boolean } = {}) =>
    request<SpacesResponse>(`/spaces${query({ search: params.search, refresh: params.refresh })}`),
  /**
   * 某個 Space 的最近 N 則訊息（**討論串回覆混在裡面**，Google 沒有參數可以排除）。
   * 帶 `thread_name` 就改成回傳完整的那一串——用來把被 limit 切斷的討論串補齊。
   */
  messages: (params: { space_id: string; limit?: number; thread_name?: string }) =>
    request<MessagesResponse>(
      `/messages${query({
        space_id: params.space_id,
        limit: params.limit,
        thread_name: params.thread_name,
      })}`,
    ),
  styles: () => request<StylesResponse>('/styles', {}, { skipAuthRedirect: true }),
  summaries: (limit = 50) => request<SummariesResponse>(`/summaries${query({ limit })}`),
  publish: (body: { space_id: string; text: string; thread_name?: string | null }) =>
    post<PublishResponse>('/publish', body as unknown as Json),

  // ── Phase 2 ────────────────────────────────────────────
  mentions: (params: { state?: MentionState; limit?: number; with_content?: boolean } = {}) =>
    request<MentionsResponse>(
      `/mentions${query({
        state: params.state,
        limit: params.limit,
        with_content: params.with_content ?? true,
      })}`,
    ),
  updateMention: (id: number, state: MentionState) =>
    request<Mention>(`/mentions/${id}`, { method: 'PATCH', body: JSON.stringify({ state }) }),
  refreshMentions: () => post<MentionRefreshResponse>('/mentions/refresh'),
  /**
   * 讀回這一則**已經存下來**的最新草稿。
   *
   * 沒有草稿時後端回 404 `DRAFT_NOT_FOUND`，那是**正常狀態**不是錯誤
   * （多數 Mention 本來就沒產過草稿），呼叫端要自己吞掉。
   */
  storedDraft: (id: number) => request<StoredDraft>(`/mentions/${id}/draft`),
  sendReply: (
    id: number,
    body: { text: string; draft_id?: number | null; merge_mention_ids?: number[] },
  ) => post<ReplyResponse>(`/mentions/${id}/reply`, body as unknown as Json),

  // ── 參考專案（ADR-0006）────────────────────────────────
  codeProjects: () => request<{ projects: CodeProject[] }>('/code-projects'),
  createCodeProject: (body: {
    name: string
    repo_path: string
    branches: Partial<Record<CodeEnvironment, string>>
    default_env?: CodeEnvironment
  }) => post<CodeProject>('/code-projects', body as unknown as Json),
  updateCodeProject: (
    id: number,
    body: Partial<{
      name: string
      repo_path: string
      branches: Partial<Record<CodeEnvironment, string>>
      default_env: CodeEnvironment
      enabled: boolean
    }>,
  ) =>
    request<CodeProject>(`/code-projects/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  deleteCodeProject: (id: number) =>
    request<{ deleted: boolean }>(`/code-projects/${id}`, { method: 'DELETE' }),
  /** 重新確認路徑與分支還在。設定頁的「重新檢查」用。 */
  verifyCodeProject: (id: number) => post<CodeProject>(`/code-projects/${id}/verify`),

  // ── 維運 ────────────────────────────────────────────────
  usage: (days = 14) => request<UsageResponse>(`/usage${query({ days })}`),
  /**
   * 服務健康狀態。**未登入也能打**——「後端起來了嗎、AI 供應商設好了嗎」
   * 正是還沒登入時最需要問的事，所以診斷頁排在登入 gate 之前。
   */
  health: () => request<HealthResponse>('/health', {}, { skipAuthRedirect: true }),
}

/** 串流端點的絕對路徑，交給 lib/sse.ts 使用。 */
export const streamUrls = {
  summarize: () => `${API_BASE}/summarize/stream`,
  draft: (mentionId: number) => `${API_BASE}/mentions/${mentionId}/draft/stream`,
}

export type StyleValue = SummaryStyleValue

/** 把任意錯誤轉成可直接顯示的繁體中文訊息。 */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return '發生未預期的錯誤'
}
