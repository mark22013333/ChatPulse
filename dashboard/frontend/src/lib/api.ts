import type {
  AIConfig,
  AuthResult,
  AuthStatus,
  Me,
  Preferences,
  Mention,
  MentionRefreshResponse,
  MentionState,
  MentionsResponse,
  PublishResponse,
  ReplyResponse,
  SpacesResponse,
  StylesResponse,
  SummariesResponse,
  SummaryStyleValue,
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
  /** `default_provider` 傳 null＝清掉偏好、沿用伺服器預設。 */
  updatePreferences: (body: { default_provider?: string | null }) =>
    request<Preferences>('/preferences', { method: 'PATCH', body: JSON.stringify(body) }),

  // ── Phase 1 ────────────────────────────────────────────
  spaces: (params: { search?: string; refresh?: boolean } = {}) =>
    request<SpacesResponse>(`/spaces${query({ search: params.search, refresh: params.refresh })}`),
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
  sendReply: (id: number, body: { text: string; draft_id?: number | null }) =>
    post<ReplyResponse>(`/mentions/${id}/reply`, body as unknown as Json),

  // ── 維運 ────────────────────────────────────────────────
  usage: (days = 14) => request<UsageResponse>(`/usage${query({ days })}`),
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
