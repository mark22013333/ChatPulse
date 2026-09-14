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
  少數錯誤會多一個 `error.detail`（**物件**），放前端需要照數字處理的結構化補充，
  例如「重產會清掉幾筆已選的專案」。**沒有這個欄位是常態**，前端要當它可能不在；
  它出現時才是權威，不要從 `message` 那句中文裡用正則挖數字。
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
| 404 | `PERSONA_NOT_FOUND` | 找不到指定的 Persona（**只在「這一次明確指定」時拋**；偏好裡的 id 失效是降級成不使用，不是錯誤） |
| 404 | `REPLY_PROMPT_NOT_FOUND` | 找不到指定的 Reply Prompt Preset。同上，偏好失效不拋 |
| 404 | `WORK_REPO_NOT_FOUND` | 找不到指定的「我的工作 repo」登錄（ADR-0008）。**刻意與 `CODE_PROJECT_NOT_FOUND` 分開**：那是參考專案（查程式碼用），這是工作 repo（產工時草稿用），兩份登錄各有自己的表、id 序號與設定頁，合碼會把人送去錯的那一頁 |
| 409 | `WORK_REPO_UNAVAILABLE` | 登錄還在，但本機路徑已經不是可讀的 git repo（磁碟沒掛上／專案被搬走）。與 `WORK_REPO_NOT_FOUND` 分開的判準是下一步不同：這個要去改路徑，不必重新登錄。與 `CODE_PROJECT_UNAVAILABLE` 同族但不同碼——後果不同，那邊是查到錯環境的程式碼，這邊是靜默漏掉那天某個 repo 的 commit |
| 409 | `PERSONA_INVALID` | 這份 Persona 淨化後沒有可用的風格資訊，或已被停用。**東西存在但不能用**，與 `CODE_PROJECT_UNAVAILABLE` 同族。淨化後為空是**可預期的正常結果**，不是 bug |
| 409 | `SEPIA_UNAVAILABLE` | 要求潤稿但 vendored 規則檔不可用。**在 SSE 開始之前就回**。這與「潤稿跑了但完整性檢查沒過」不同——後者退回未潤稿版本並在 `polish.fallback_reason` 說明，不拋錯 |
| 429 | `CHAT_RATE_LIMITED` / `GEMINI_QUOTA_EXCEEDED` / `CLAUDE_QUOTA_EXCEEDED` | 上游限流，帶 `Retry-After`。兩個 AI 的配額分開列，前端可據此建議「換一個供應商試試」 |
| 502 | `CHAT_API_ERROR` / `GEMINI_API_ERROR` / `CLAUDE_API_ERROR` | 上游非預期回應。`CLAUDE_API_ERROR` 指的是「Claude 這條路徑出錯」，**不是**已移除的 `claude_api` 供應商——`claude_cli` 也用這個碼 |
| 502 | `PERSONA_SOURCE_ERROR` | 從外部來源取得 Persona 失敗（網路、404、超過大小上限、格式不對）。**刻意與 `PERSONA_INVALID` 分開**：這個是「東西拿不到」（換網址、稍後再試、確認 repo 是公開的），那個是「拿到了但讀不出東西」（換來源或改用手動填寫）。502 而不是 400，因為問題出在外部服務或外部內容，不是呼叫端的參數 |
| 500 | `CONFIGURATION_ERROR` | 伺服器設定不完整（例如缺 GOOGLE_API_KEY） |
| 500 | `INTERNAL_ERROR` | 未預期錯誤的兜底 |

**只出現在 SSE 事件、沒有 HTTP 狀態對應的 code**（前端要一併處理）：

| code | 情境 |
| :--- | :--- |
| `NO_MESSAGES` | 該聊天室在指定範圍內沒有可摘要的對話。串流已開始，只能以事件告知 |
| `NO_EVIDENCE` | 那一天沒有任何 commit 或發言可以產工時草稿（ADR-0008）。比照 `NO_MESSAGES`：**這不是錯誤狀態，是「那天沒有留下痕跡」**，所以不落地任何草稿 |
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
  "default_provider": "claude_cli",
  "default_code_project_id": 3, "default_code_environment": "production",
  "default_reply_tone": "professional", "default_persona_id": 4,
  "default_reply_prompt_id": 2, "default_sepia_enabled": true }
```
回傳更新後的 preferences，另含 `updated_at`（ISO 8601 UTC）。
`default_provider` 傳空字串等同清除（回到伺服器預設）；傳非法值回 `400 INVALID_PARAMETER`。

**`null` 的語意在新舊欄位之間不一致，這是實作上真實存在的差異，呼叫端必須知道：**

| 欄位 | 送 `null` 的意思 |
| :--- | :--- |
| `pinned_space_ids`／`default_limit`／`default_style`／`default_provider`／`default_code_project_id`／`default_code_environment` | **不改**（等同沒帶這個欄位） |
| `default_reply_tone`／`default_persona_id`／`default_reply_prompt_id`／`default_sepia_enabled` | **清除**（回到「沒有預設值」的狀態） |

四個回覆設定欄位需要「清除」這個動作，是因為「不使用 Persona」「不套用預設口氣」「不要自動潤稿」都是使用者會主動選的狀態，必須存得下去；而舊欄位的 `null`＝不改已經被現有前端依賴，改掉會弄壞它。handler 靠 `model_fields_set` 區分「沒帶這個欄位」與「帶了 null」，對應到 repository 的 `UNSET` 哨兵或 `None`。

`default_reply_tone` 是**回話的語氣**，`default_style` 是**摘要的章節結構**——兩者不同層次也不同值域，刻意不共用欄位（ADR-0007）。這四個欄位**刻意沒有外鍵**：Persona 或 Preset 被刪除時偏好會留著一個失效的 id，產草稿時降級成「不使用」並記 log，不擋住功能。

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
需登入。query：`space_id`（**必填**）、`limit`（1~1000，預設 50）、
`thread_name`（選填，給了就回**整個討論串**而不是最近 N 則；必須屬於 `space_id`，
否則回 400）。
訊息**由舊到新**排序（方便閱讀脈絡），內容為當下即時取回。

**討論串回覆本來就在裡面**——`messages.list` 回的是扁平訊息流，Google 沒有參數
可以排除它們。但「最近 N 則」常把一串切成片段，所以每則都帶 `thread_name`，
呼叫端可以自己分組；要補齊被切掉的部分就帶 `thread_name` 再打一次。

**只有附件、沒有文字的訊息也會回**（`text` 是空字串、靠 `attachment_note` 才看得出
有東西）。這個端點原本 `if not text: continue`，於是「@某人 ＋ 一張截圖」整則消失，
表現是「我要 20 則怎麼只有 17 則」而找不到原因。
```json
{
  "space_id": "spaces/AAAAxLxqJxY",
  "space_name": "0.暫存",
  "thread_name": null,
  "count": 50,
  "messages": [ { "name": "spaces/../messages/..", "sender": "鄭浩宇",
                 "sender_id": "users/1098272650197...", "time": "2026-09-04 08:53",
                 "text": "...", "attachment_note": "[圖片：shot.png（AI 未讀取內容）]",
                 "thread_name": "spaces/../threads/.." } ]
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
  "merge_mention_ids": [46],
  "code_refs": [ { "project_id": 3, "environment": "production" } ], "code_terms": [],
  "tone_id": "engineer", "persona_id": 4, "custom_prompt": null,
  "custom_prompt_id": 2, "sepia_enabled": true }
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

`code_refs` 也**預設空陣列**（ADR-0006：不自動挑專案），上限 2 筆。
`environment` 可省略，省略時用專案的 `default_env`；合法值只有
`production`／`uat`／`dev`。**送同一個 `project_id` 兩次配不同 `environment`，
就是「比對正式與 UAT」**——「這是不是 bug」這類問題最有價值的用法。
每筆可另帶 `paths`（明確指定檔案，跳過關鍵字搜尋）。
`code_terms` 覆寫自動抽詞。

**回覆設定（7.4／ADR-0007）**——五個欄位全部選填，只作用於〈建議回話〉：

| 欄位 | 型別 | 語意 |
| :--- | :--- | :--- |
| `tone_id` | `string` | Reply Tone。合法值見 `GET /api/v1/reply-tones`。`null`＝用 Viewer 偏好，偏好也沒有就**完全不介入**（產出與這個功能存在之前逐字相同） |
| `persona_id` | `int` | 要套用的 Persona。`null`＝用 Viewer 偏好；**`0` 是「這一次明確不使用」** |
| `custom_prompt` | `string` | 這一次直接輸入的自訂要求（上限見下方）。有值時**忽略** `custom_prompt_id` |
| `custom_prompt_id` | `int` | 要套用的 Reply Prompt Preset。`null`＝用 Viewer 偏好；**`0` 是「這一次明確不使用」** |
| `sepia_enabled` | `bool` | 要不要跑潤稿。`null`＝用 Viewer 偏好，偏好也沒有就**不潤**（系統預設關閉） |

`persona_id` 與 `custom_prompt_id` 需要 `0` 這個哨兵，是因為 `null` 已被「沿用 Viewer 偏好」佔用——Viewer 設了預設 Persona 之後，「這次不要用」沒有別的方式表達，送 `null` 會被當成照偏好來。`0` 不可能是合法的 AUTOINCREMENT id。

解析優先序：**Per Draft Override > Viewer Preference > System Default**。兩種「找不到」處置不同，這是呼叫端最容易誤判的地方：

- **這一次明確指定的 id 找不到** → `404 PERSONA_NOT_FOUND` / `404 REPLY_PROMPT_NOT_FOUND`。指定的 Persona 被停用 → `409 PERSONA_INVALID`。`tone_id` 不是合法值 → `400 INVALID_PARAMETER`。
- **Viewer 偏好裡的 id 或 tone 失效** → 降級成「不使用」並記 log，請求正常進行。

`sepia_enabled: true` 但規則檔不可用 → `409 SEPIA_UNAVAILABLE`。
`custom_prompt` 過長 → `400 INVALID_PARAMETER`（訊息會帶實際字數與上限）。
**以上全部在 SSE 開始之前就回**，不是 error 事件。

專案不存在回 **404 `CODE_PROJECT_NOT_FOUND`**、環境沒有對應分支回
**400 `INVALID_PARAMETER`**——**這兩個都在 SSE 開始前就回**，不是 error 事件。
路徑不是 repo 回 **409 `CODE_PROJECT_UNAVAILABLE`**、分支不存在回
**409 `CODE_BRANCH_NOT_FOUND`**（訊息會附上現有分支清單）。

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
  "code_refs": [ { "project_name": "智慧客服後端", "environment": "production",
                   "environment_label": "正式環境", "branch": "main", "commit_sha": "a3f91c2",
                   "commit_date": "2026-08-28T11:04:12+08:00", "terms": ["retry_backoff"],
                   "hit_count": 2, "files": ["app/services/session.py"],
                   "truncated": false, "notes": [] } ],
  "code_skipped": [],
  "provider": "claude_cli", "model": "claude-cli:opus",
  "image_count": 1, "images_skipped": [],
  "reply": { "tone": "engineer", "tone_label": "工程師協作",
             "persona_id": 4, "persona_name": "羅振宇",
             "custom_prompt": true, "custom_prompt_id": 2, "sepia": true } }
```
`answering` 是這份草稿會回掉的全部 Mention（合併時 > 1 則）。**送出時要照它走**，
不要沿用送出前的勾選——伺服器實際採用的才算數。

`context.anchor_count` 是「這次要回**幾件事**」，與 `answering` 的長度**不是同一件事**：
系統會自動把「從我上次發言到現在、對方講了而我還沒回」的訊息都標成要回的，
所以就算只選了一則 Mention，`anchor_count` 也可能是 2（對方問了兩件事都還沒回）。
同一群連發（間隔 < `DRAFT_ANCHOR_RUN_GAP_MINUTES`）算一件；上限
`DRAFT_ANCHOR_MAX_CLUSTERS`，超出的較舊問題仍在脈絡裡但不標成要回的。
`thread_message_count` 保留舊名，值是 `context.message_count`（這次送進模型的對話則數）。
`coverage: "partial"` 代表系統沒能取回錨點周圍的完整對話（那則太舊了），prompt 會據此
要模型更保守，前端會把則數標成橘色。

`code_refs` 在 `meta` 裡的用途是**讓 Viewer 在模型開口之前就看到依據對不對**：
查了哪個環境、哪個 commit、用哪些關鍵字、命中哪些檔案。搜錯環境一眼就看得到，
不必先讀完生成文字。`hit_count` 為 0 代表「查了但沒找到」，與「沒有查」是不同的事，
前端與 prompt 都必須分得出來。

`meta.reply` 是**這次實際套用的回覆設定**，用途與 `code_refs` 相同：讓 Viewer 在模型開口
之前就看到「系統以為我選了什麼」。只有實際生效的鍵會出現（沒選 tone 就沒有 `tone`），
`sepia` 一律有。**`custom_prompt` 是布林，不是全文**——自訂提示是使用者輸入，沒有必要
出現在 SSE 事件與 devtools 裡；要知道是哪一筆 preset 看 `custom_prompt_id`。

`done`：`{"type":"done","draft_id":3,"reply":null,"polish":null}`
輸出內容為兩段 Markdown：`### 🧭 脈絡分析` 與 `### ✍️ 建議回話`。

開了潤稿時 `done` 的兩個欄位會有值：

```json
{ "type": "done", "draft_id": 3,
  "reply": "（潤稿後的〈建議回話〉全文）",
  "polish": { "polisher": "sepia", "polished": true, "polish_model": "claude-cli:opus" } }
```

| 欄位 | 語意 |
| :--- | :--- |
| `reply` | 潤稿後的〈建議回話〉全文。**未潤稿時為 `null`** |
| `polish` | 潤稿 meta。**未啟用潤稿時為 `null`**。`polished: false` 時帶 `fallback_reason` |

**前端必須處理 `reply`**：潤稿後 DB 存的內容與前端串流累積的不一致，而使用者按「送出」
時送的是前端那一份。忽略這個欄位的話，開了潤稿就會把**未潤稿**的版本送到 Google Chat。
`reply` 有值時要用它取代串流累積的〈建議回話〉那一段。

`polished: false` 有兩種來源，都**不是錯誤而是降級**，但都必須讓使用者看到：
草稿裡切不到〈建議回話〉標題（潤整篇會改到脈絡分析，所以不潤），
或潤稿跑了但錨點完整性檢查沒過（退回未潤稿的版本）。兩者的 `fallback_reason` 都是
可直接顯示的繁中句子。

**事件型別沒有新增**——仍然只有 `meta`／`chunk`／`done`／`error` 四種。

### 參考專案（ADR-0006）

全部需登入，且一律限於自己的專案（`viewer_id` 為必填查詢條件，沒有「查全部」的入口）。

| 端點 | 說明 |
| :--- | :--- |
| `GET /api/v1/code-projects` | 列出，回 `{"projects":[...]}` |
| `POST /api/v1/code-projects` | 登錄，同步驗證分支 |
| `GET /api/v1/code-projects/{id}` | 單筆 |
| `PATCH /api/v1/code-projects/{id}` | 更新，同步驗證分支 |
| `DELETE /api/v1/code-projects/{id}` | 刪除 |
| `POST /api/v1/code-projects/{id}/verify` | 重新確認路徑與分支還在 |

登錄 body：
```json
{ "name": "智慧客服後端", "repo_path": "D:\work\cs-backend",
  "branches": { "production": "main", "uat": "release/uat" },
  "default_env": "production" }
```

**至少要指定一個環境的分支**，否則回 400——沒有分支對應的專案正是
「查問題時找錯環境」本身，而這個功能的存在理由就是防這件事。
`repo_path` 必須是絕對路徑。`default_env` 必須有對應的分支。

`branches` 在 PATCH 時是**整組取代**，不是逐項合併——否則「刪掉 uat 對應」
這個動作沒有辦法表達。

POST／PATCH／verify 回傳帶 `verification`：
```json
{ "id": 3, "name": "智慧客服後端", "branches": {...},
  "verification": { "repo_ok": true, "working_tree_dirty": true,
    "branches": { "production": { "branch": "main", "exists": true, "commit": "a3f91c2",
                                  "commit_date": "2026-08-28T11:04:12+08:00" },
                  "uat": { "branch": "release/uat", "exists": false, "error": "分支不存在",
                           "did_you_mean": ["release/uat-2026q3"] } } },
  "last_verify_error": "分支不存在：uat=release/uat" }
```
**分支不存在仍然建立成功**（分支可能之後才開），但 `last_verify_error` 會被記下來
讓設定頁標警告。登錄時大聲失敗，遠比產草稿時才發現便宜。

### 回覆設定（ADR-0007）

兩個靜態清單端點**不需登入**（理由同 `/styles` 與 `/providers`：登入畫面也可能要顯示
「目前沒有可用的潤稿器」）。其餘全部需登入，且**一律限於自己的資料**——`viewer_id` 是
repository 層的必填查詢條件，沒有「查全部」的入口，別人的 Persona 與 Preset 連 id
猜對了也讀不到（回 404，不是 403）。這與 ADR-0002 對 Summary 的私有標準一致。

#### `GET /api/v1/reply-tones`
**不需登入。** Reply Tone 選項，供下拉選單。
```json
{ "tones": [ { "id": "natural", "label": "自然直接", "description": "…",
               "example": "（固定的示例回話）" } ],
  "default": "natural" }
```
八個 id：`natural`／`professional`／`concise`／`friendly`／`engineer`／`soft`／
`assertive`／`custom`。**回應不含 prompt instruction**——那是送給模型的片段，
前端不需要，送出去只會變成使用者讀得到卻改不了的死資料。`example` 有送，
UI 拿它做固定預覽，不必為了預覽去打一次 AI。

與 `GET /api/v1/styles` 是**兩個不同的東西**：那個是**摘要**的章節結構
（`general`／`technical`／`action_only`），這個是**回話**的語氣。值域不共用，
不要互相套用。

#### `GET /api/v1/polishers`
**不需登入。** 潤稿器清單與可用性，形狀比照 `/providers`。
```json
{ "polishers": [ { "name": "noop", "label": "不潤稿", "available": true, "reason": "" },
                 { "name": "sepia", "label": "Sepia 潤稿", "available": true, "reason": "" } ],
  "sepia": { "name": "sepia", "version": "0.8.0",
             "source_repository": "Nanako0129/sepia", "source_ref": "v0.8.0",
             "source_commit_sha": "d8a0f948cc46a0ba0d610df7458c4e8943bfe51a",
             "license": "MIT" } }
```
`available: false` 的 `reason` 寫的是**規則檔在哪、怎麼補**——前端要顯示出來，不要吞掉。
`sepia` 是 vendored 規則的 provenance，要能一路顯示到 UI——半年後回頭看一份草稿，
必須答得出「當時用的是哪一版規則」。

**`available` 回答的是「規則裝好了嗎」，不是「現在這一刻能不能跑」。** 這個端點用的是
`ResponsePolisher.static_available()`（sepia 的實作是 `sepia.rules_available()`，只看
vendored 規則檔），刻意不含 AI 供應商的可用性——後者由 `GET /api/v1/providers` 回答。
兩者混在一個布林裡的話，前端就無法判斷該叫使用者去補裝規則、還是去設定 API key。

前端用這個欄位決定「使用 Sepia 潤稿」的勾選框能不能勾。要判斷「這一次能不能潤稿」，
仍以送 `sepia_enabled: true` 時是否回 `409 SEPIA_UNAVAILABLE` 為準——
`polish()` 之前會跑含供應商的完整檢查（`available()`）。

#### `GET /api/v1/personas`
需登入。自己的 Persona 清單 ＋ 可用的來源型別。
```json
{ "personas": [ { "id": 4, "name": "羅振宇", "description": "…",
                  "source_type": "github", "source_repository": "fxp/persona-distill-skills",
                  "source_url": null, "source_ref": "main",
                  "source_commit_sha": "24c9850e4a8bbb8b3b1ab797b428163fa3c07066",
                  "source_hash": "sha256:…", "enabled": true,
                  "imported_at": "…", "refreshed_at": null,
                  "created_at": "…", "updated_at": "…",
                  "profile": { "name": "羅振宇", "thinking_style": ["…"],
                               "communication_style": ["…"], "response_preferences": {},
                               "avoid": ["…"], "boundaries": ["…"], "schema_version": 1 } } ],
  "sources": [ { "name": "github", "label": "GitHub 公開 repository" },
               { "name": "manual", "label": "自訂（手動填寫）" } ] }
```
`profile` 是**已淨化**的結構化資料，這是 Persona 唯一能進 prompt 的形狀。
**列表不含 `raw_source`**（遠端原文有 20 KB 上下，放進列表會讓回應體積隨 Persona
數量線性膨脹；更重要的是少一條「有人把它接回 prompt」的路徑）。
`source_type` 的合法值：`github`／`manual`，另有別名 `url`（走 GitHub source 的網址入口）。

#### `GET /api/v1/personas/{persona_id}`
需登入。單筆，形狀同列表的元素。query：`include_raw`（預設 `false`；
`true` 才附上 `raw_source`——**僅供 debug 與「淨化掉了什麼」的比對**）。
不存在回 `404 PERSONA_NOT_FOUND`。

#### `POST /api/v1/personas`
需登入。手動建立。兩種輸入形態，二選一：
```json
{ "name": "我的風格", "description": "…", "raw_text": "（一段 markdown 風格描述）" }
```
```json
{ "name": "我的風格", "description": "…",
  "profile": { "thinking_style": ["…"], "communication_style": ["…"], "avoid": ["…"] } }
```
回 `{"persona": {...}, "created": true}`。

**手填內容一樣走完整淨化流程。** 使用者最可能的填寫方式就是從某處複製一份 skill
全文貼進來，那與遠端抓下來的沒有任何差別。淨化後沒有剩下可用風格資訊回
`409 PERSONA_INVALID`（**這是可預期的正常結果**）；同名已存在回 `400 INVALID_PARAMETER`。

#### `PATCH /api/v1/personas/{persona_id}`
需登入。body 任一欄位可省略：
```json
{ "name": "新名字", "description": "…", "enabled": false }
```
回傳更新後的 Persona。不存在回 `404 PERSONA_NOT_FOUND`。

**只能改名稱、簡介與啟用狀態，不能改 `profile`。** profile 只能由匯入流程產生，
因為那條路徑保證跑過淨化——開一個「直接寫 profile_json」的入口等於開一個繞過淨化的後門。
要改內容就重新匯入或刪掉重建。

#### `DELETE /api/v1/personas/{persona_id}`
需登入。回 `{"deleted": true}`。不存在回 `404 PERSONA_NOT_FOUND`。
**刻意不檢查有沒有偏好指向它**：`preferences.default_persona_id` 沒有外鍵，
刪掉之後那筆偏好會失效，產草稿時降級成「不使用」並記 log。

#### `POST /api/v1/personas/import`
需登入。從外部來源匯入，並**固定版本**。兩種形態：
```json
{ "source_type": "github", "repository": "fxp/persona-distill-skills",
  "persona": "luozhenyu-perspective", "ref": "main", "name": "羅振宇" }
```
```json
{ "source_type": "url", "url": "https://github.com/owner/repo/blob/main/skills/x/SKILL.md" }
```
`ref` 可省略（用預設分支），但**一律會解析成 commit SHA 存下來**。`name` 是覆寫顯示
名稱——來源檔案的 `name` 常常是 skill 識別字（實測到 `luozhenyu-perspective`）而不是人名。

```json
{ "persona": { "id": 4, "...": "..." }, "created": true }
```
**同名視為「更新」而不是衝突**（`created: false`）：使用者按「更新 Persona」走的就是
同一條匯入流程，報 409 會逼他先刪再匯入，中間那段時間偏好會斷掉。

取不到／格式不對／超過大小上限回 `502 PERSONA_SOURCE_ERROR`；
取到了但淨化後為空回 `409 PERSONA_INVALID`；
`source_type` 不合法或判斷不出名稱回 `400 INVALID_PARAMETER`。

#### `POST /api/v1/personas/{persona_id}/refresh`
需登入。重新從原來的來源取檔（會更新 `source_commit_sha`）。無 body。
```json
{ "persona": { "...": "..." }, "created": false, "changed": true }
```
**刻意用原本的 `source_ref`（分支名）重新解析，而不是沿用舊的 commit SHA**——
「更新」的意思就是去看那個分支現在長什麼樣。`changed` 由 `source_hash` 比對得出，
因為「更新後內容有沒有真的變」沒有別的方式判斷。

`source_type` 是 `manual` 的回 `400 INVALID_PARAMETER`（自訂 Persona 沒有外部來源）。
不存在回 `404 PERSONA_NOT_FOUND`，其餘錯誤同 import。

#### `GET /api/v1/personas/sources/{source_type}/list`
需登入。列出某個來源 repo 有哪些 Persona 可以匯入。
query：`repository`（**必填**，`owner/repo`）、`ref`（選填）。
```json
{ "personas": [ { "id": "luozhenyu-perspective", "name": "luozhenyu-perspective",
                  "path": "skills/luozhenyu-perspective/SKILL.md", "size": 8421,
                  "repository": "fxp/persona-distill-skills",
                  "ref": "main", "commit_sha": "24c9850e…" } ] }
```
**需登入但不讀 Viewer 的資料**——這裡的登入檢查純粹當認證閘門，避免變成一個未登入
就能用的對外 GET 代理（寫法比照 `POST /api/v1/code-projects/{id}/verify`）。
GitHub 未認證每小時只有 60 次呼叫，前端不要拿它做輸入即時提示。

#### `GET /api/v1/reply-prompts`
需登入。自己的 Reply Prompt Preset 清單。
```json
{ "reply_prompts": [ { "id": 2, "name": "客戶回覆", "description": "…",
                       "prompt": "（自訂要求全文）",
                       "created_at": "…", "updated_at": "…" } ] }
```

#### `POST /api/v1/reply-prompts`
需登入。
```json
{ "name": "客戶回覆", "description": "對外窗口用", "prompt": "（自訂要求全文）" }
```
回傳建立後的單筆（形狀同列表元素）。名稱或內容為空、內容超過字數上限、
同名已存在，一律回 `400 INVALID_PARAMETER`（訊息會帶實際字數與上限）。

#### `PATCH /api/v1/reply-prompts/{prompt_id}`
需登入。body 任一欄位可省略：`name`／`description`／`prompt`。
回傳更新後的單筆。帶了 `prompt` 但內容為空或過長回 `400 INVALID_PARAMETER`；
不存在回 `404 REPLY_PROMPT_NOT_FOUND`。

#### `DELETE /api/v1/reply-prompts/{prompt_id}`
需登入。回 `{"deleted": true}`。不存在回 `404 REPLY_PROMPT_NOT_FOUND`。
與刪 Persona 同理，不檢查 `preferences.default_reply_prompt_id` 是否指向它。

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

## Draft Worklog（工時草稿，ADR-0008）

從 Viewer 的 git commit 與 Chat 發言產生每日工時草稿。

目前完成：「我的工作 repo」登錄、**週檢視**（`GET /worklogs/week`）、**草稿產生**
（`POST /worklogs/{date}/draft/stream`）。**尚未實作**：`GET`／`PUT /worklogs/{date}`、
`POST /worklogs/{date}/submit`、`GET /zplanner/issues/search`、`GET /zplanner/permissions`
（候選與權限目前只在草稿的 SSE `meta` 裡一次帶回，沒有獨立的搜尋端點）。

### 我的工作 repo

全部需登入，且一律限於自己的登錄（`viewer_id` 是 repository 層的必填查詢條件，
沒有「查全部」的入口；別人的登錄連 id 猜對了也讀不到，回 404 不是 403）。

> **與「參考專案」是兩份不同的登錄，不要共用。** 那邊（`/code-projects`，ADR-0006）
> 是為了**查程式碼回答問題**而登的，必須釘死是哪個環境的哪一版；這邊是為了
> **回想這天做了什麼**。兩者的分支語意正好相反，見下方 `branch_filter`。

| 端點 | 說明 |
| :--- | :--- |
| `GET /api/v1/work-repos` | 列出，回 `{"repos":[...]}` |
| `POST /api/v1/work-repos` | 登錄，同步驗證路徑 |
| `PATCH /api/v1/work-repos/{id}` | 部分更新，同步驗證路徑 |
| `DELETE /api/v1/work-repos/{id}` | 刪除，回 `{"deleted": true}` |
| `POST /api/v1/work-repos/{id}/verify` | 重新確認路徑還是可讀的 git repo |

登錄 body：
```json
{ "name": "智慧客服後端", "repo_path": "/Users/me/repos/cs-backend",
  "author_emails": ["me@corp.com", "me@personal.dev"],
  "branch_filter": [],
  "default_zplanner_project_id": 42, "default_zplanner_project_key": "PEI" }
```

- `repo_path` 必須是**絕對路徑**（相對路徑會相對於伺服器的工作目錄去解，
  那跟使用者心裡想的不是同一個地方）。
- `author_emails` 是**陣列，不是單值**：同一個人在不同 repo 常常設不同的
  `user.email`（公司信箱、個人信箱、GitHub 的 noreply）。只填一個會讓另一個
  信箱的 commit **靜默漏掉**——草稿照樣產得出來，只是少了那幾筆工時。
  不確定自己在這個 repo 用哪個信箱時，先建立、再按「重新檢查」，
  `verification.detected_emails` 會列出來。
- `branch_filter` **空陣列＝掃 `--all`（全部分支）**，不是「不掃」。有值才當白名單。
  ⚠️ 這與 `/code-projects` 的「環境→釘死一個分支」**相反**，不是疏忽：那邊拿錯
  環境等於給出錯的答案，這邊漏掉一個還沒合併的 feature 分支就等於漏掉那天的工作。
- `default_zplanner_project_id` / `_key` **只用來把該專案的 issue 排進候選清單並
  標示來源，絕不自動填**（ADR-0008 的唯一硬規則：專案歸屬一律由 Viewer 在確認
  畫面選）。猜錯的那次長得跟猜對的一模一樣，而 ZPlanner 的工時會進月結，
  月結後不能直接改、要走 correction-request。

**兩條唯一性限制**，違反時回 `400 INVALID_PARAMETER`（先查一次才回得出看得懂的
訊息，否則會是 `UNIQUE` 撞出來的 500）：
- `(viewer_id, name)` — 同名只是難看。
- `(viewer_id, repo_path)` — **同一個 repo 登錄兩次會讓同一批 commit 被算兩遍，
  時數會分錯。** 這條是為了算對工時，不是為了美觀。

`PATCH` 的語意：
- `author_emails`／`branch_filter` 是**整組取代**，不是逐項合併（比照 code-projects
  的 `branches`）——否則「把某個舊信箱拿掉」這個動作沒有辦法表達。
- `default_zplanner_project_id`／`_key` 送 **`null` 代表「清除」**，欄位**沒出現**
  才是「不改」（比照 `PATCH /preferences` 的回覆設定欄位）。「這個 repo 沒有預設
  專案」是使用者會主動選的狀態，必須存得下去。
- 其餘欄位沒帶就是不改。

POST／PATCH／verify 都回帶 `verification` 的單筆：
```json
{ "id": 3, "name": "智慧客服後端", "repo_path": "/Users/me/repos/cs-backend",
  "author_emails": ["me@corp.com"], "branch_filter": [],
  "default_zplanner_project_id": 42, "default_zplanner_project_key": "PEI",
  "enabled": true, "last_verified_at": "2026-09-13T03:12:07+00:00",
  "last_verify_error": null, "created_at": "…", "updated_at": "…",
  "verification": { "ok": true, "error": null, "head_sha": "a3f91c2",
                    "detected_emails": ["me@corp.com", "someone-else@corp.com"] } }
```

- `ok: false` 時 `error` 是可直接顯示的繁中句子（`路徑不存在或不是資料夾`／
  `這個路徑不是 git repo`），同一份字串也會寫進 `last_verify_error`。
- **路徑當下不可讀仍然建立成功**（外接磁碟可能還沒掛上、專案可能待會才 clone），
  但 `last_verify_error` 會被記下來讓設定頁標警告。理由同參考專案的
  「分支不存在仍然建立成功」——登錄時大聲提醒，遠比產草稿時才發現便宜。
- `head_sha` 在剛 `git init`、還沒有任何 commit 的 repo 是 `null`。那**不是**錯誤，
  `ok` 仍然是 `true`。
- `detected_emails` 掃最近 200 筆 commit 的作者信箱，去重後**最近用過的排前面**
  （只差大小寫的視為同一個）。它是**給人看的參考**，後端不會自動寫進
  `author_emails`——同一條哲學：由人指定，系統不自動發現。
- 讀 git 一律**不碰工作區**（ADR-0006 鐵律）：只跑 `rev-parse` 與 `log`，
  不 checkout、不動 index，使用者未提交的修改不受影響。

`verification` 的形狀**刻意與 code-projects 的不同**（那邊是
`{repo_ok, branches, working_tree_dirty, error}`）：這裡沒有釘死的分支可以驗，
硬套同一個形狀只會多出永遠空著的 `branches` 欄位。

### `GET /api/v1/worklogs/week`

需登入。query：`week_start`（`YYYY-MM-DD`，必填）。合併**本地草稿狀態**與
**ZPlanner 既有工時**，給週欄用。

```json
{ "week_start": "2026-09-07", "week_end": "2026-09-13",
  "days": [
    { "work_date": "2026-09-07", "weekday": 1,
      "draft": { "id": 12, "work_date": "2026-09-07", "status": "draft",
                 "total_hours": "8.00", "tz_offset": "+08:00",
                 "generation_meta": { "...": "..." },
                 "generated_at": "…", "created_at": "…", "updated_at": "…",
                 "entry_count": 3, "hours": "8.00", "allocated_hours": "8.00",
                 "submitted_count": 0, "failed_count": 0, "unknown_count": 0,
                 "unassigned_count": 3 },
      "zplanner": { "hours": "8.00", "worklog_count": 2 } },
    { "work_date": "2026-09-08", "weekday": 2, "draft": null, "zplanner": null } ],
  "totals": { "draft_hours": "8.00", "days_with_draft": 1, "days_submitted": 0,
              "entries_unassigned": 3 },
  "zplanner": { "available": true, "total_hours": "16.00", "worklog_count": 5,
                "target_hours": "40.00", "completion_pct": 38.8 } }
```

- **七天一律回滿**，沒有草稿的那天 `draft` 是 `null`。前端不必自己補空格——
  「這天還沒產草稿」與「這天不在回應裡」必須是同一件事，否則週欄會少一列。
- `week_start` 照傳進來的那天往後算七天，**不強制是星期一**（週的起點是前端的
  顯示決定）。回應一定同時帶 `week_start` 與 `week_end`，讓兩邊對得起來。
  `weekday` 是 ISO 星期（1＝一），給「週六/日 休」那兩列用。
- `draft.total_hours` 是 Viewer 輸入的總時數，`draft.hours` 是**各筆之和**——
  兩者不保證相等（湊 0.5 倍數必然有餘數），真正會送出的是後者。
- `unassigned_count` 是還沒選專案的筆數。**未選擋住送出**（ADR-0008），
  週欄要看得出「這天還差幾筆沒決定」。
- 🔴 **ZPlanner 不可用時不會讓這支端點失敗**：`zplanner` 變成
  `{"available": false, "reason": "…"}`，`days[].zplanner` 全部是 `null`，
  **本地草稿照常回**。沒設 ZPlanner 的人仍然看得到自己的草稿。
  `reason` 是可直接顯示的繁中句子（未設定／連線逾時／回應形狀不認得）。
- `zplanner.worklog_count` 數的是**工時筆數**，不是有填工時的天數。ZPlanner 的
  `days[]` 沒有筆數欄位，實際筆數在 `entries` 陣列的長度（2026-09-13 實測）。
- `target_hours` 與 `completion_pct` **由 ZPlanner 算**，取不到時這兩個欄位
  整個不出現（不是 `null`）。前端據此決定要不要畫「還差多少」那一行。
  ⚠️ **不會補預設值**：編一個 40 小時的目標會讓完成率變成看起來精確的假數字。
  `target_hours` 是字串（與其他時數一致），`completion_pct` 是 float。
- 🔴 **非本週一律看不到 ZPlanner 的工時。** ZPlanner 的 `GET /api/worklogs/weekly/`
  **只回本週**，`week_start` 送了沒有作用（2026-09-14 實測五種輸入皆回同一週，
  OpenAPI schema 也沒宣告這個參數）。所以切到上週時，`days[].zplanner` 全是 `null`、
  `zplanner.total_hours` 是 `"0.00"`，並帶 `week_mismatch`——**那不代表那幾天沒填**。
  要讀非本週的工時得走 ZPlanner 的匯出端點（`date_from`／`date_to`），見
  `core/zplanner_client.export_worklogs`；本端點尚未接上它。
- 🔴 **`zplanner.total_hours` 永遠等於 `days[].zplanner.hours` 的和。** 除了上面那個
  成因，`week_start` 不強制是星期一也會讓兩邊錯開（本端點的七天由前端決定起點，
  ZPlanner 那側固定週一起算）。落在窗口外的日期一律**不算進總計**，否則表頭與
  下面的格子會對不起來（實測：請求週日起的 `2026-09-06`，格子合計 40.0 小時、
  表頭卻寫 44.00 小時、完成率 110%——那是 ZPlanner 那一週的和）。
- 錯開時多一個 `zplanner.week_mismatch`，且 **`target_hours` 與 `completion_pct`
  整個不出現**（它們描述的是 ZPlanner 那一週，套到這七天上就是算錯的數字）：
  ```json
  { "available": true, "total_hours": "40.00", "worklog_count": 5,
    "week_mismatch": { "requested_week_start": "2026-09-06",
                       "requested_week_end": "2026-09-12",
                       "zplanner_week_start": "2026-09-07",
                       "zplanner_week_end": "2026-09-13",
                       "reason": "…可直接顯示的繁中句子…" } }
  ```
  `available` 仍然是 `true`：逐日資料照樣對得起來，整段報廢反而少給了能用的東西。
  ZPlanner 回應沒有 `week_start`／`week_end`（舊版或裸清單）時不判定錯開——
  沒有東西可以比對，就只靠逐日資料的日期自然過濾。

### `POST /api/v1/worklogs/{date}/draft/stream`

需登入。**SSE**。`{date}` 是 `YYYY-MM-DD`。body：
```json
{ "tz_offset": "+08:00", "repo_ids": [3], "space_ids": ["spaces/AAA"],
  "total_hours": 8, "provider": null, "force": false }
```

| 欄位 | 語意 |
| :--- | :--- |
| `tz_offset` | **必填**，`+08:00`／`+0800`／`Z`。日界由它決定——「半夜一點的 commit 算哪一天」必須有寫得出來的依據，不是伺服器剛好設成什麼。非法值 `400` |
| `repo_ids` | 省略／空 ＝ 全部**啟用中**的工作 repo。指定了但那筆不存在或已停用 → `404 WORK_REPO_NOT_FOUND` |
| `space_ids` | 省略 ＝ 釘選的 Space ∪ 當天有 Mention 命中的 Space。**絕不掃全部 436 個**；非 `spaces/` 開頭 → `400` |
| `total_hours` | **省略就不分配**，`entries[].hours` 一律 `null` |
| `provider` | 這一階段**不接 AI**，只回進 `meta.provider` 備查 |
| `force` | 預設 `false`。明示「我知道重產會清掉已經選好的專案，還是要重來」。**蓋不過「已送進 ZPlanner」那條線**（見下方重產規則） |

事件序：`meta` → 每群一個 `chunk` → `done`。**事件型別不新增**，仍只有
`meta`／`chunk`／`done`／`error`。以上參數錯誤全部**在 SSE 開始之前**回 4xx。

`meta` ＝ `DayEvidence.to_meta()`（`work_date`／`tz_offset`／`since`／`until`／
`repos[]`／`repos_skipped[]`／`spaces[]`／`spaces_skipped[]`／`commits[]`／`chat[]`）
再加上 `clusters[]`、`zplanner`、`provider`／`model`／`note_source`。

```json
{ "type": "meta", "work_date": "2026-09-13", "tz_offset": "+08:00",
  "commits": [ { "ref": "commit:a3f…", "kind": "commit", "sha": "a3f…", "short_sha": "a3f91c2",
                 "subject": "fix: PEI-133 匯出逾時", "authored_at": "2026-09-13T10:00:00+08:00",
                 "author_email": "me@corp.com", "insertions": 42, "deletions": 3,
                 "files_changed": 2, "branches": ["main"], "terms": ["PEI-133"],
                 "weight_input": 3.8067, "source_label": "cs-backend" } ],
  "chat": [ { "ref": "chat:spaces/AAA/messages/m1", "kind": "chat",
              "message_name": "spaces/AAA/messages/m1", "space_id": "spaces/AAA",
              "space_name": "客服專案群", "is_mention": false, "char_count": 24,
              "occurred_at": "2026-09-13T11:00:00+08:00" } ],
  "clusters": [ { "key": "issue:PEI-133", "kind": "issue", "label": "PEI-133",
                  "reason": "2 個 commit 都提到 PEI-133（跨 cs-backend、cs-frontend）",
                  "weight": 7.61, "evidence_refs": ["commit:a3f…", "commit:b7c…"],
                  "terms": ["PEI-133", "export"], "occurred_at": "2026-09-13T10:00:00+08:00",
                  "issue_key": "PEI-133", "commit_count": 2, "chat_count": 0,
                  "source_labels": ["cs-backend", "cs-frontend"],
                  "candidates": [ { "issue_id": 9, "issue_key": "PEI-133", "title": "訂單匯出效能",
                                    "project_id": 42, "project_key": "PEI",
                                    "source": "issue_key", "confidence": "strong",
                                    "reason": "你在 commit 訊息裡直接寫了 PEI-133",
                                    "can_worklog": true, "permission_checked": true,
                                    "permission_reason": "" } ] } ],
  "zplanner": { "available": true, "reason": "", "queried_terms": ["PEI-133"],
                "worklogable_count": 4, "permission_checked": false,
                "permission_email": "me@corp.com", "permission_email_source": "viewer_email",
                "permission_note": "…", "projects_checked": [ { "project_id": 42,
                "role": "developer", "can_worklog": true, "checked": true, "reason": "" } ],
                "errors": [] },
  "provider": null, "model": null, "note_source": "commit_subject" }
```

**證據欄只能顯示 `meta` 裡有的東西**（設計紅線：任何無法回溯到 `meta` 某個欄位的
東西不准進證據欄），所以這裡漏掉什麼，前端就永遠顯示不出來。
`clusters[].evidence_refs` 用字串指回 `commits[]`／`chat[]` 的 `ref`，
**前端拿到指不到的 ref 直接丟棄**。

`repos[].truncated`／`spaces[].truncated` 的語意是「**有東西沒進來**」，不是
「掃到了上限」。它是使用者唯一能察覺「那天可能漏了證據」的訊號，所以只有
真的可能漏掉時才是 `true`：

- **repo**：掃描預算（`WORKLOG_GIT_SCAN_LIMIT`）用完，或那天的 commit 超過
  單日保留上限。
  ⚠️ **`git log` 刻意沒有 `--until` 上界**——`--since`／`--until` 濾的是
  committer date，而 rebase 可以發生在寫程式之後的**任何時候**（把功能分支
  擺兩週再 rebase 是日常）。固定的上界只覆蓋得了「N 小時內完成的 rebase」，
  超過的會被 git 直接濾掉，而且 `truncated` 還是 `false`、`skipped` 還是空的
  ——使用者完全看不出那天少了東西。代價是視窗變成 `[日界−72h, ∞)`，而 git log
  是 committer date 由新到舊輸出，`-n` 砍掉的正好是最舊的那一端（＝目標日）。
- **space**：翻頁預算用完卻還沒翻到日界，或自己的發言超過保留上限。
  ⚠️ Google Chat 的 `list_messages_since` 只支援 `createTime >`、預設 ASC，
  所以額度**會被別人的閒聊吃光**。翻頁的停止條件因此是「訊息時間越過日界」，
  不是「拿到 N 則」——後者會讓當天下午之後自己的發言整批消失。

分群規則（由強到弱，先命中先歸群，實作在 `core/worklog_clustering.py`）：

| 序 | 規則 | `label` | `kind` |
| :--- | :--- | :--- | :--- |
| 1 | commit message／發言裡的 **issue key**（`PEI-133`） | 那個 key | `issue` |
| 2 | 其餘 commit 按 **repo** | repo 名 | `repo` |
| 3 | Chat 按 **Space** | Space 名 | `space` |

規則 1 最強而且**會跨 repo**（同一張單在前後端各改一半是常態）。
`ADR-0008`／`UTF-8`／`RFC-3339` 這類「長得像單號」的前綴**會被濾掉**，不自成一群。
每群的 `weight` 是成員 `weight_input` 的總和，**每筆證據只進一群**（進兩群等於那天
的時數憑空多出一塊）。`reason` 是**可直接顯示的中文句子**——分群是啟發式，猜錯時
使用者唯一能察覺的方式就是讀那句話。

`chunk` 每群一個，帶 `index`／`cluster_key`／`text`：
```json
{ "type": "chunk", "index": 0, "cluster_key": "issue:PEI-133", "text": "fix: PEI-133 匯出逾時；PEI-133 加上重試" }
```
⚠️ **這一階段的 `text` 不是 AI 寫的**，是該群前兩筆 commit subject 以「；」相接
（超長截斷），`meta.note_source` 會標 `commit_subject`。AI 潤飾是後續的事。

`done`：
```json
{ "type": "done", "draft_id": 12,
  "entries": [ { "id": 31, "seq": 1, "cluster_key": "issue:PEI-133", "label": "PEI-133",
                 "reason": "2 個 commit 都提到 PEI-133（跨 cs-backend、cs-frontend）",
                 "issue_id": null, "issue_key": null,
                 "hours": "3.50", "allocated_hours": "3.50", "rounded_up": true,
                 "weight": 7.61, "note": "fix: PEI-133 匯出逾時；PEI-133 加上重試",
                 "evidence_refs": ["commit:a3f…"], "candidates": [ "…同 meta" ],
                 "state": "pending" } ],
  "allocation": { "total_hours": "8.00", "entries": [ { "index": 0, "issue_key": "PEI-133",
                  "allocated_hours": "3.50", "rounded_up": true, "merged_into": null } ],
                  "adjustments": [] } }
```

- 🔴 **`entries[].issue_id`／`issue_key` 一律是 `null`**——系統只給候選，選擇權在
  Viewer（ADR-0008 的唯一硬規則）。一份看起來合理的自動歸屬，代價由一個月後發現
  填錯的人支付（月結後不能直接改，要走 correction-request）。落地的那一份也是空的。
  （`allocation.entries[].issue_key` 是**分配演算法的 tie-break 依據**，不是歸屬，
  前端不可拿它去預選。）
- `total_hours` 省略時 `hours`／`allocated_hours`／`allocation` 全部是 `null`——
  使用者還沒決定總時數之前，不該看到憑空冒出來的數字。
- 草稿**一天一份**（`UNIQUE(viewer_id, work_date)`），同一天再產一次是覆蓋——
  覆蓋是**整組 DELETE 再 INSERT**，所以重產前有兩道守門，兩道都會把「會影響
  幾筆」放進 `error.detail`，讓前端說得出具體數字而不必從中文句子裡挖：

  | 情況 | 回應 | `force` 蓋得過嗎 |
  | :--- | :--- | :--- |
  | 有任何一筆送進 ZPlanner（`zplanner_worklog_id` 有值） | `409 WORKLOG_ALREADY_SUBMITTED`，`detail: { submitted_count, force_allowed: false }` | ❌ **不行**。worklog id 弄丟就永遠對不回來，之後只剩 correction-request |
  | 沒送出、但有任何一筆已經選好 issue（`issue_id`／`issue_key` 有值） | `400 INVALID_PARAMETER`，`detail: { assigned_count, assigned_issue_keys, force_allowed: true }` | ✅ 帶 `force: true` 放行 |

  🔴 選專案是這個功能裡最花心力的一步（ADR-0008：系統只給候選，一律由 Viewer 選），
  所以**不准安靜覆蓋**——擋下來、說清楚會清掉幾筆，由人決定。`force: true` 之後重產
  出來的 `issue_id`／`issue_key` 一樣全是 `null`，要重選一次。
  ⚠️ 已送出的那道守門在串流開始前檢查一次，**並且在真正寫入的那個 transaction 裡
  再檢查一次**：中間隔著 git 掃描與 ZPlanner 呼叫（可達數十秒），這段時間內若有另一
  條路徑把工時送出去，只靠前面那次檢查會把剛拿到的 worklog id 靜默抹掉。串流已經
  開始時，這道守門會變成 `error` 事件（`code: "WORKLOG_ALREADY_SUBMITTED"`），**草稿
  一筆都不會被改到**。
- 錯誤主體的 `error.detail` **只有拋的人刻意給結構化補充時才出現**，型別是物件。
  其餘情況（大多數錯誤）沒有這個欄位——字串型的 detail 是內部訊息，只進 log。
- 候選 issue 用 `list_worklogable_issues(q=群的關鍵字)` 撈，**`q` 必填**
  （ZPlanner 對空字串回 0 筆而不是全部）。`confidence` 只有三級並**一律附一句可見
  的理由**：不做排名徽章、不做百分比、不做進度條——要讓人讀理由而不是讀名次。
- 權限預檢用 `list_project_role_permissions` ＋ `list_project_members`。
  查不到就 `can_worklog: null` ＋ `permission_checked: false`，**不假裝有權限**。
  ⚠️ `meta.zplanner.permission_checked` 現階段**一律是 `false`**：比對用的是登入的
  Google 信箱，而 `members[].user_email` 未必是同一個帳號；要確定得先有
  `preferences.zplanner_email`（該欄位尚未存在）。
- 一次最多預檢 5 個專案、每群最多 3 個候選（清單永不封閉，其餘靠搜尋框）。
- **ZPlanner 不可用時照樣產得出草稿**：`meta.zplanner.available` 為 `false`、
  候選是空陣列，其餘一切照常。

`error`：沒有任何 commit／發言時發 **`NO_EVIDENCE`**（SSE-only code，比照
`NO_MESSAGES`），**且不落地任何草稿**——硬產一份空草稿會讓使用者把時數分給不存在
的證據。其餘串流中的錯誤照 8.3 轉成 `error` 事件。

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
3. **v1 的 ZPlanner 匯出按鈕已於 v2.0 移除**，不要在 Action Items 那裡保留任何
   剪貼簿 handler。（**Draft Worklog 是另一件事**，見 ADR-0008 與規格 5.3——它走真正的
   API 串接、有自己的畫面。目前完成到：ZPlanner client 層、「我的工作 repo」
   登錄端點與設定頁、工時工作台的導航與路由骨架、週檢視與草稿產生（SSE）。
   單日的讀取／整組取代與送出（`GET`／`PUT`／`submit`）尚未實作。）
4. Action Items 萃取：從摘要 Markdown 中抓 `• [負責人] 任務` 這種行，轉為可勾選項目，
   支援「複製為 Markdown」。
5. 空間清單有 436 個，列表需要虛擬滾動或分頁，並提供名稱搜尋。
