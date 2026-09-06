# ChatPulse API 契約（v2）

> 這份文件是前後端之間的**單一事實來源**。規格對應：`SPECIFICATION.md` 8.1~8.4。
> 後端實作：`dashboard/api/server.py`。
> 路徑約定：**space_id 一律不放 path**（它內含斜線）。GET 走 query string，POST 走 body。

## 通則

- 所有端點前綴 `/api/v1`
- 認證：Cookie `chatpulse_session`（HttpOnly）。未登入的受保護端點回 `401 NOT_AUTHENTICATED`
- 非串流錯誤格式固定：
  ```json
  { "error": { "code": "SPACE_NOT_FOUND", "message": "找不到指定的聊天室" } }
  ```
- `limit` 的**範圍**一律 1~1000，超出回 `400 INVALID_PARAMETER`。**預設值多為 50，但依端點而異**（`/mentions` 是 200——那是「列幾筆 Mention」，與「一個 Space 抓幾則對話」不是同一件事），各端點的預設值見下方各節
- `style` 只接受 `general` / `technical` / `action_only`
- `provider` 選填，決定用哪個 AI 供應商。可用值：`claude_cli`／`gemini`
  兩個實作，加上 `claude`／`auto` 兩個**別名**（別名會挑第一個現在可用的實作）。
  `claude_api` 曾是合法值，已於 2026-09-05 移除，現在傳它會回 `400 INVALID_PARAMETER`。
  省略時依序取：Viewer 的 `default_provider` 偏好 → 伺服器的 `CHATPULSE_AI_PROVIDER` → `claude`。
  名稱不合法時回 `400 INVALID_PARAMETER`，**在進串流之前就擋掉**

### 錯誤碼對照

| HTTP | code | 情境 |
| :--- | :--- | :--- |
| 400 | `INVALID_PARAMETER` | 參數格式錯誤、limit 超出 1~1000、style 非法 |
| 401 | `NOT_AUTHENTICATED` | 未登入或 session 過期 |
| 403 | `SPACE_FORBIDDEN` | 不是該聊天室成員 |
| 404 | `SPACE_NOT_FOUND` / `MENTION_NOT_FOUND` / `ROUTE_NOT_FOUND` | 目標不存在；`ROUTE_NOT_FOUND` 專指 API 路徑打錯（刻意與 SPACE_NOT_FOUND 分開，否則前端會把「端點打錯」顯示成「聊天室不見了」） |
| 429 | `CHAT_RATE_LIMITED` / `GEMINI_QUOTA_EXCEEDED` / `CLAUDE_QUOTA_EXCEEDED` | 上游限流，帶 `Retry-After`。兩個 AI 的配額分開列，前端可據此建議「換一個供應商試試」 |
| 502 | `CHAT_API_ERROR` / `GEMINI_API_ERROR` / `CLAUDE_API_ERROR` | 上游非預期回應。`CLAUDE_API_ERROR` 指的是「Claude 這條路徑出錯」，**不是**已移除的 `claude_api` 供應商——`claude_cli` 也用這個碼 |
| 500 | `CONFIGURATION_ERROR` | 伺服器設定不完整（例如缺 GOOGLE_API_KEY） |
| 500 | `INTERNAL_ERROR` | 未預期錯誤的兜底 |

**只出現在 SSE 事件、沒有 HTTP 狀態對應的 code**（前端要一併處理）：

| code | 情境 |
| :--- | :--- |
| `NO_MESSAGES` | 該聊天室在指定範圍內沒有可摘要的對話。串流已開始，只能以事件告知 |
| `INTERNAL_ERROR` | 串流開始之後才發生的未預期錯誤 |

## SSE 事件格式（8.3）

所有串流端點共用。每個 frame 是單行 JSON，以 `data: ` 開頭、`\n\n` 結尾。
**錯誤以事件傳遞，不中斷連線**（HTTP 200 已送出，無法再改狀態碼）。

```
data: {"type":"meta","space":"1.BU2-PG","message_count":50}

data: {"type":"chunk","text":"本週討論集中在"}

data: {"type":"done"}
```

```
data: {"type":"error","code":"GEMINI_QUOTA_EXCEEDED","message":"Gemini 配額已用盡"}
```

回應標頭固定含 `Cache-Control: no-cache`、`X-Accel-Buffering: no`。
前端以 `fetch` + `response.body.getReader()` 消費（**不是** `EventSource`——端點是 POST）。
收到 `done` 或 `error` 後即關閉讀取。

---

## 認證

### `GET /api/v1/auth/status`
不需登入。回傳目前狀態，供前端決定顯示登入畫面或主畫面。
```json
{
  "authenticated": false,
  "viewer": null,
  "legacy_token_available": true,
  "can_bootstrap": true,
  "viewer_count": 0
}
```

### `POST /api/v1/auth/login`
啟動 Google OAuth（loopback flow，會在伺服器所在機器開啟瀏覽器）。
scope 含 chat 三項 ＋ `openid`/`userinfo.email`/`userinfo.profile`。
成功時設定 session cookie 並回傳 viewer。逾時（預設 180 秒）回 `408`。
```json
{ "authenticated": true, "viewer": { "id": 1, "google_user_id": "users/123", "email": "...", "display_name": "..." } }
```

### `POST /api/v1/auth/bootstrap`
把既有的 `config/google_chat_token.json`（Phase 1 之前的三 scope token）匯入
`credentials` 表並建立 session。身分解析失敗時回 `401`，訊息會說明要設
`CHATPULSE_BOOTSTRAP_USER_ID`。回應同 login。

### `POST /api/v1/auth/logout`
清除 session cookie。回 `{"authenticated": false}`。

### `GET /api/v1/me`
需登入。
```json
{
  "viewer": { "id": 1, "google_user_id": "users/123", "email": "...", "display_name": "..." },
  "scopes": ["https://www.googleapis.com/auth/chat.spaces.readonly", "..."],
  "has_identity_scope": true,
  "preferences": { "pinned_space_ids": [], "default_limit": 50, "default_style": "general",
                   "default_provider": null },
  "ai": { "default": "claude", "providers": [ /* 同 GET /api/v1/providers */ ] },
  "collector": { "implementation": "polling", "interval_seconds": 45, "running": true,
                 "last_polled_at": "2026-09-05T01:23:45+00:00", "last_error": null,
                 "last_run_stats": { "new_mentions": 0, "spaces_polled": 2, "spaces_total": 436, "api_calls": 3, "elapsed_seconds": 0.8 } },
  "mention_counts": { "pending": 3, "resolved": 12 }
}
```

### `PATCH /api/v1/preferences`
需登入。body 任一欄位可省略：
```json
{ "pinned_space_ids": ["spaces/AAA"], "default_limit": 100, "default_style": "technical",
  "default_provider": "claude_cli" }
```
回傳更新後的 preferences，另含 `updated_at`（ISO 8601 UTC）。
`default_provider` 傳空字串等同清除（回到伺服器預設）；傳非法值回 `400 INVALID_PARAMETER`。

---

## Phase 1 既有能力

### `GET /api/v1/spaces`
需登入。query：`search`（名稱模糊比對，可省略）、`refresh`（`true` 強制刷新，跳過 5 分鐘快取）。
```json
{
  "count": 436,
  "total": 436,
  "cached": true,
  "cached_at": "2026-09-05T01:20:00+00:00",
  "spaces": [
    { "id": "spaces/AAAAxLxqJxY", "displayName": "0.暫存", "type": "SPACE",
      "threadingState": "THREADED_MESSAGES",
      "lastActiveTime": "2026-09-04T17:35:29.773695Z", "memberCount": 3, "pinned": false }
  ]
}
```
`threadingState` 來自 Google 的 `spaceThreadingState`，Draft Reply 用它決定脈絡的形狀。
**不可單獨拿它判斷「有沒有討論串」**：2026-09-06 實測 436 個 Space，私訊回報的是
`THREADED_MESSAGES` 而不是官方文件寫的 `UNTHREADED_MESSAGES`。判準要與 `type` 取聯集，
見 `core/draft_context.is_flat_space()`。

### `GET /api/v1/messages`
需登入。query：`space_id`（**必填**）、`limit`（1~1000，預設 50）。
訊息**由舊到新**排序（方便閱讀脈絡），內容為當下即時取回。
```json
{
  "space_id": "spaces/AAAAxLxqJxY",
  "space_name": "0.暫存",
  "count": 50,
  "messages": [ { "name": "spaces/../messages/..", "sender": "鄭浩宇",
                 "sender_id": "users/1098272650197...", "time": "2026-09-04 08:53", "text": "..." } ]
}
```

### `POST /api/v1/summarize/stream`
需登入。**SSE**。body：
```json
{ "space_id": "spaces/AAAAxLxqJxY", "limit": 50, "style": "general", "provider": "claude_cli" }
```
事件序：`meta` → 多個 `chunk` → `done`。
`meta` 內容：`{"type":"meta","space":"0.暫存","space_id":"spaces/...","message_count":50,"style":"general","provider":"claude_cli","model":"claude-cli:opus","image_count":2,"images_skipped":[]}`

`image_count` 是**實際送進模型**的圖片張數；`images_skipped` 是被略過的原因清單
（超出張數上限、超出 token 預算、下載失敗、聊天室被列入排除清單…）。
被略過的圖仍以 `[圖片：檔名（AI 未讀取內容）]` 出現在對話文本裡——
差別是模型知道有圖但看不到內容。供應商不支援視覺時 `image_count` 為 0
且**完全不會下載**（能力檢查發生在下載之前）。
**`provider` 與 `model` 是伺服器實際使用的值**（別名已展開），前端顯示「用了哪個」時要以此為準，不要用送出前的選擇。
串流結束時後端會把完整摘要寫入 `summaries`（僅本人可見），`done` 事件帶 `summary_id`：
`{"type":"done","summary_id":12}`

### `GET /api/v1/providers`
**不需登入**（登入畫面也可能要顯示「目前沒有可用的 AI 供應商」）。
```json
{
  "default": "claude",
  "providers": [
    { "name": "claude_cli", "label": "Claude Code（本機 CLI，用你現有的訂閱）", "model": "claude-cli:opus",
      "available": true, "reason": "使用本機 /Users/cheng/.local/bin/claude", "supports_vision": true },
    { "name": "gemini", "label": "Gemini（Google AI Studio）", "model": "gemini-3.6-flash",
      "available": true, "reason": "使用 GOOGLE_API_KEY", "supports_vision": true }
  ]
}
```
`default` 可能是別名（`claude`／`auto`）。`available: false` 的 `reason` 寫的是
**該設哪個環境變數、該裝什麼**——前端要顯示出來，不要吞掉。

### `GET /api/v1/styles`
不需登入。摘要風格選項，供下拉選單。
```json
{ "styles": [ { "value": "general", "label": "通用" }, { "value": "technical", "label": "技術細節" }, { "value": "action_only", "label": "只要待辦" } ] }
```

### `POST /api/v1/publish`
需登入。以 **Viewer 本人身分**送出（非 Bot）。前端**必須**先做二次確認對話框。
```json
{ "space_id": "spaces/AAAAxLxqJxY", "text": "訊息內容", "thread_name": null }
```
```json
{ "status": "success", "message_id": "spaces/../messages/..", "createTime": "...", "thread_name": "spaces/../threads/.." }
```

### `GET /api/v1/summaries`
需登入。只回傳**自己**產生的 Summary（ADR-0002）。query：`limit`（1~1000，預設 50——
與其他入口共用 `core.config` 的同一組上下限，不另設數字）。
```json
{ "count": 3, "summaries": [ { "id": 12, "space_id": "...", "space_name": "0.暫存",
  "style": "general", "message_count": 50, "content_md": "...", "created_at": "..." } ] }
```

---

## Phase 2 Mention 收件匣

### `GET /api/v1/mentions`
需登入。query：`state`（`pending` / `resolved`，省略為全部）、`limit`（預設 200）、
`with_content`（預設 `true`；`true` 時後端即時向 Google Chat 取回訊息內容——
mentions 表**只存識別資訊**，不存內容）。
```json
{
  "count": 2,
  "counts": { "pending": 2, "resolved": 5 },
  "mentions": [
    {
      "id": 7,
      "space_id": "spaces/AAAAxLxqJxY",
      "space_name": "0.暫存",
      "message_name": "spaces/../messages/..",
      "thread_name": "spaces/../threads/..",
      "sender_display": "王小明",
      "sender_id": "users/1141234...",
      "create_time": "2026-09-05T01:10:00Z",
      "state": "pending",
      "resolved_at": null,
      "text": "（即時取回的訊息內容；取不到時為 null）",
      "content_error": null
    }
  ]
}
```

### `PATCH /api/v1/mentions/{id}`
需登入。body `{"state": "resolved"}` 或 `{"state": "pending"}`。
回傳更新後的 mention（不含 text）。不存在回 `404 MENTION_NOT_FOUND`。

### `POST /api/v1/mentions/refresh`
需登入。同步跑一輪採集，回傳統計。用於「立即檢查」按鈕。
```json
{ "ran": true, "stats": { "new_mentions": 1, "total_matched": 1, "spaces_polled": 2,
  "spaces_total": 436, "api_calls": 3, "elapsed_seconds": 0.9, "full_sweep": false, "errors": [] } }
```

### `POST /api/v1/mentions/{id}/draft/stream`
需登入。**SSE**。產生 Draft Reply。body：
```json
{ "reference_space_ids": ["spaces/BBB", "spaces/CCC"], "limit": 50, "provider": "claude_cli",
  "merge_mention_ids": [46] }
```
`reference_space_ids` **預設空陣列**（7.3：不自動選擇 Reference Space）。

`merge_mention_ids`：一起回成一則的其他 Mention（收件匣多選）。URL 上那則是主要的，
回話會送到它的討論串。三條限制，違反回 **400 INVALID_PARAMETER**（SSE 開始前就擋，
不是變成 error 事件）：同一個 Space、分串聊天室還要同一個討論串、不能是已處理的；
一次最多 5 則。**跨討論串是最危險的一種**——放行的話回話會送出成功，
但只進得了其中一串，另一串的人永遠看不到，而兩則都被標成已處理。

脈絡的**形狀由 Space 的結構語意決定**（`core/draft_context.py`），三種：

| `mode` | 何時 | 取什麼 |
| :--- | :--- | :--- |
| `flat_window` | 私訊、或 `threadingState=UNTHREADED_MESSAGES` 的聊天室 | 錨點前 15 後 10 則，48h 上界＋錨點前保底 6 則 |
| `thread` | 分串聊天室且該串 ≥ 2 則 | 整串（上限 `DRAFT_THREAD_LIMIT`=60）。**行為與 2026-09-06 之前一致** |
| `thread_thin` | 分串聊天室但該串只有 1 則（有人 @ 你還沒人回） | 原串 ＋ 一個**獨立且帶警語**的跨串小窗（8 則） |

圖片：**只取被 @ 的那則與其脈絡，Reference Space 的圖不取**。理由是成本——
參考群組可能有好幾個、每個 50 則，圖片全抓會吃光預算；而真正需要看到的是
「@ 我的那則自己帶的截圖」。被 @ 的那則（含同一人的連發）享有最高優先序。
圖片的取樣母體與文字脈絡是**兩份清單**：`flat_window` 的圖只取錨點連發 ＋ 之前 6 則
（不含錨點之後、不含跨串小窗）。
事件序：`meta` → 多個 `chunk` → `done`。
`meta`：
```json
{ "type": "meta", "mention_id": 7, "space": "0.暫存", "thread_message_count": 12,
  "context": {
    "mode": "flat_window", "message_count": 12, "coverage": "full",
    "time_range": { "start": "2026-09-04T05:37:22Z", "end": "2026-09-06T12:32:57Z" },
    "anchor_count": 2,
    "blocks": [ { "kind": "flat_window", "label": "這個一對一私訊在這幾則前後的連續對話（前 6 則、後 5 則）", "count": 12 } ]
  },
  "answering": [ { "mention_id": 45, "sender_display": "李小明", "create_time": "2026-09-06T11:33:28Z" },
                 { "mention_id": 46, "sender_display": "李小明", "create_time": "2026-09-06T12:32:57Z" } ],
  "reference_spaces": [ { "space_id": "spaces/BBB", "space_name": "1.BU2-PG", "message_count": 50 } ],
  "provider": "claude_cli", "model": "claude-cli:opus",
  "image_count": 1, "images_skipped": [] }
```
`answering` 是這份草稿會回掉的全部 Mention（合併時 > 1 則）。**送出時要照它走**，
不要沿用送出前的勾選——伺服器實際採用的才算數。
`thread_message_count` 保留舊名，值是 `context.message_count`（這次送進模型的對話則數）。
`coverage: "partial"` 代表系統沒能取回錨點周圍的完整對話（那則太舊了），prompt 會據此
要模型更保守，前端會把則數標成橘色。
`done`：`{"type":"done","draft_id":3}`
輸出內容為兩段 Markdown：`### 🧭 脈絡分析` 與 `### ✍️ 建議回話`。

### `POST /api/v1/mentions/{id}/reply`
需登入。送出回話（**前端必須先二次確認**）。
```json
{ "text": "編輯後的回話內容", "draft_id": 3, "merge_mention_ids": [46] }
```
行為：以 Viewer 身分回到 `thread_name` 所在討論串 → 該 Mention **自動標記為 resolved**。
`merge_mention_ids`（草稿 meta 的 `answering` 帶回來的）只送出**一則**訊息，
但把被合併的那幾則一起標成 resolved。驗證與草稿端點共用同一個函式。
```json
{ "status": "success", "message_id": "...", "createTime": "...",
  "mention": { "id": 7, "state": "resolved", "resolved_at": "..." },
  "mentions": [ { "id": 7, "state": "resolved", "...": "..." },
                { "id": 8, "state": "resolved", "...": "..." } ] }
```
`mention` 是主要那則（既有欄位）；`mentions` 是這次實際結掉的全部。
訊息送出後任何一則標記失敗都**不會**讓請求變成 500——那會讓人以為沒送出而再送一次，
對方就收到兩則。失敗只寫 log。

---

## 維運

### `GET /api/v1/health`
不需登入。
```json
{ "status": "ok", "db": "wal",
  "ai_provider_default": "claude", "ai_provider_active": "claude_cli",
  "gemini_configured": true, "collector_running": true,
  "collector_implementation": "polling", "viewer_count": 1 }
```

### `GET /api/v1/usage`
需登入。R-2：**本人**每日 token 用量（帶 `viewer_id` 查詢——用量看得出誰哪幾天在用多少，
與 ADR-0002 對 Summary 的私有標準一致）。query：`days`（1~90，預設 14，以日期下界過濾）。
```json
{ "usage": [ { "day": "2026-09-05", "model": "gemini-3.6-flash", "prompt_tokens": 12000,
  "output_tokens": 3000, "total_tokens": 15000, "calls": 4 } ] }
```

---

## 前端注意事項

1. **SSE 一律用 `fetch` + `getReader()`**，不可用 `EventSource`（端點是 POST）。
   元件 unmount 時要 `AbortController.abort()`，否則串流會繼續跑。
2. **任何送出動作都要二次確認對話框**（5.4、7.2 步驟 6）。
3. **ZPlanner 相關功能已於 v2.0 移除**，不要保留任何按鈕或 handler。
4. Action Items 萃取：從摘要 Markdown 中抓 `• [負責人] 任務` 這種行，轉為可勾選項目，
   支援「複製為 Markdown」。
5. 空間清單有 436 個，列表需要虛擬滾動或分頁，並提供名稱搜尋。
