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
                   "default_provider": null, "default_models": { "claude_cli": "opus" } },
  "ai": { "default": "claude", "providers": [ /* 同 GET /api/v1/providers */ ],
          "default_models": { "claude_cli": "opus" } },
  "collector": { "implementation": "polling", "interval_seconds": 45, "running": true,
                 "last_polled_at": "2026-09-05T01:23:45+00:00", "last_error": null,
                 "last_run_stats": { "new_mentions": 0, "spaces_polled": 2, "spaces_total": 436, "api_calls": 3, "elapsed_seconds": 0.8 } },
  "mention_counts": { "pending": 3, "resolved": 12 }
}
```
`ai.default_models` 是 `{供應商實作名: model 裸值}`，**沒設過的供應商不會出現在物件裡**
（前端要顯示「跟著該供應商的預設走」，不是空字串）。`preferences.default_models` 是同一份資料，
`ai` 那份是給 AI 相關 UI 用的、與 `providers[]` 放在一起。偏好的 JSON 壞掉時兩邊都回 `{}`
而不是 500——偏好壞掉不該讓整個頁面打不開。

### `PATCH /api/v1/preferences`
需登入。body 任一欄位可省略：
```json
{ "pinned_space_ids": ["spaces/AAA"], "default_limit": 100, "default_style": "technical",
  "default_provider": "claude_cli",
  "default_code_project_id": 3, "default_code_environment": "production",
  "default_reply_tone": "professional", "default_persona_id": 4,
  "default_reply_prompt_id": 2, "default_sepia_enabled": true,
  "default_models": { "claude_cli": "opus" } }
```
回傳更新後的 preferences，另含 `updated_at`（ISO 8601 UTC）。
`default_provider` 傳空字串等同清除（回到伺服器預設）；傳非法值回 `400 INVALID_PARAMETER`。

**`default_models`**：每個供應商各自的預設 model（**裸值**，例 `opus`，不是 `claude-cli:opus`）。
一個供應商一格，而不是單一個 `default_model`——model 的意義依附在供應商上，`opus` 對 Gemini
毫無意義，存單一字串的話 Viewer 一換供應商偏好就變成非法值。

| 規則 | 說明 |
| :--- | :--- |
| 鍵 | 必須是**具體實作名**（`claude_cli`／`gemini`）。別名 `claude`／`auto` 會展開成不同實作，存了就對應不回來 → `400 INVALID_PARAMETER` |
| 值 | 非空字串；**不驗白名單**（`models` 只是常用值，自訂輸入是這個功能的重點） |
| 值送 `null`／空字串 | 清除**那一個供應商**的偏好，回到「跟著該供應商的預設走」 |
| 只送部分鍵 | **merge，不是整份覆寫**——沒帶到的供應商偏好原封不動。前端只會送剛改動的那一個，整份覆寫會把另一個供應商存好的值弄丟 |
| 整個欄位沒帶、送 `null`、或送 `{}` | 不動既有偏好，**也不會寫入**（`updated_at` 不動）。沒有任何一格要改的時候去寫一次同樣的 JSON，只會讓「這筆偏好上次什麼時候被動過」變成不可信的欄位 |

⚠️ merge 的基底讀自資料庫。**讀不到就整支失敗（5xx），不會退化成整份覆寫**——
寧可這次 PATCH 不成功，也不要靜默寫進一份少了幾格的資料還回 200。

**`null` 的語意在新舊欄位之間不一致，這是實作上真實存在的差異，呼叫端必須知道：**

| 欄位 | 送 `null` 的意思 |
| :--- | :--- |
| `pinned_space_ids`／`default_limit`／`default_style`／`default_provider`／`default_code_project_id`／`default_code_environment` | **不改**（等同沒帶這個欄位） |
| `default_reply_tone`／`default_persona_id`／`default_reply_prompt_id`／`default_sepia_enabled` | **清除**（回到「沒有預設值」的狀態） |
| `default_models` | 整個欄位送 `null`＝不改（連寫入都不會發生）；**物件裡某個鍵的值**送 `null`＝清除那一個供應商（見上表） |

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
      "lastActiveTime": "2026-09-04T17:35:29.773695Z", "memberCount": 3, "pinned": false,
      "lastSummaryAt": "2026-09-04T09:12:03+00:00" }
  ]
}
```
`lastSummaryAt`（`string | null`）：**目前這位 Viewer 自己**最近一次對這個 Space 產生
Summary 的時間（`summaries.created_at`，UTC、秒精度、`+00:00` 結尾）；沒產生過是 `null`，
欄位一定存在。只看 `owner_viewer_id` 是自己的列（ADR-0002），別人的 Summary 不影響它。
每次請求都即時查 DB（一次 `GROUP BY space_id`，不是每個 Space 一次），**不進** Space
清單的 5 分鐘快取——否則剛產生的 Summary 要等快取過期才看得到。
摘要工作台的「Summary」欄拿它跟 `lastActiveTime` 比（`lib/spaceTable.ts` 的
`summaryStatusOf`）：`null`＝90 天內沒有（Summary 只保留 90 天，更早的已清除）；活動較晚＝有新活動；否則＝已是最新；
`lastActiveTime` 缺少或兩者任一沒有時區標記／解析不了＝顯示「—」，不猜成已是最新。
⚠️ 兩個時間的表示法不同（Google 是 `Z`＋微秒，DB 是 `+00:00`＋秒），比較一律換成
時間點再比，不可比字串。Summary 保留 90 天（九節），過期後會回到 `null`。
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
{ "space_id": "spaces/AAAAxLxqJxY", "limit": 50, "style": "general", "provider": "claude_cli",
  "model": "opus" }
```
`model` 選填，是 model 的**裸值**（`opus`，不是識別字 `claude-cli:opus`）。
生效順序見下方〈選擇 model〉。
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
      "model_id": "opus", "models": ["opus", "sonnet", "haiku"],
      "available": true, "reason": "使用本機 /Users/cheng/.local/bin/claude", "supports_vision": true },
    { "name": "gemini", "label": "Gemini（Google AI Studio）", "model": "gemini-3.6-flash",
      "model_id": "gemini-3.6-flash", "models": ["gemini-3.6-flash"],
      "available": true, "reason": "使用 GOOGLE_API_KEY", "supports_vision": true }
  ]
}
```
`default` 可能是別名（`claude`／`auto`）。`available: false` 的 `reason` 寫的是
**該設哪個環境變數、該裝什麼**——前端要顯示出來，不要吞掉。

🔴 **`model` 與 `model_id` 是兩個不同的東西，不要互換**：

| 欄位 | 是什麼 | 用在哪 |
| :--- | :--- | :--- |
| `model` | **識別字**，記進 `token_usage.model` 的完整字串。ClaudeCLI 會加前綴（`claude-cli:opus`），Gemini 與裸值相同。**語意與既有行為完全不變** | 顯示「這份產出用了什麼」、用量統計 |
| `model_id` | **裸值**，目前生效的那一個（`opus`） | ModelSelect 的目前值 |
| `models` | 常用裸值清單，**不是完整列舉** | ModelSelect 的下拉選項；Viewer 也可以自己打字輸入清單外的值 |

把識別字當裸值送回來會變成 `claude-cli:claude-cli:opus` 流進 CLI 參數，所以
**API 參數、偏好、白名單一律是裸值**，識別字只出現在 `model` 這個既有欄位裡。

#### 選擇 model（兩個串流端點共用）

`POST /summarize/stream` 與 `POST /mentions/{id}/draft/stream` 的 body 可以帶選填的
`model`（裸值）。生效順序是**先展開、再查**，**兩個端點同一份實作**
（`dashboard/api/server.py` 的 `resolve_model()`）：

0. 先把供應商別名展開成具體實作名（`auto` → `claude_cli`）
1. body 的 `model` 有值 → 用它
2. 否則查 Viewer 的 `default_models[展開後的實作名]`
3. 否則不指定，由供應商用自己的 config 預設
4. 最後用**展開後的實作名**建構供應商

**為什麼要先展開**：供應商別名 ＋ model 的組合會被 `resolve()` 拒絕（`auto` 自己會
換供應商，把 `opus` 餵給 Gemini 要到真的呼叫 API 才看得出來壞了）。先展開就完全避開
那個組合，而偏好查的是展開後那一格，不會錯配。`auto` 挑供應商的時機不變。

值**不驗白名單**（自訂輸入是重點）。**body 不帶 `model` 是正常且常見的路徑**
（前端的「跟隨預設」選項就是不帶），不是錯誤。

🔴 唯一的硬規則：**body 明確帶了 `model`、而 `provider` 是 `auto`** →
`400 INVALID_PARAMETER`。偏好是 per-provider 的對照表，查哪一格不會錯；body 的單一
`model` 值不帶供應商資訊，「auto ＋ 某個 model」本身就是矛盾的組合。前端選了 `auto`
時要停用 ModelSelect，並讓 Viewer 看得出原因。

`POST /worklogs/{date}/draft/stream` **沒有** `model` 欄位：那個端點這一階段不接 AI，
收下一個保證無效的參數比不收更糟——呼叫端會以為自己選的模型生效了。

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
  "model": "opus", "merge_mention_ids": [46],
  "code_refs": [ { "project_id": 3, "environment": "production" } ], "code_terms": [],
  "tone_id": "engineer", "persona_id": 4, "custom_prompt": null,
  "custom_prompt_id": 2, "sepia_enabled": true }
```
`reference_space_ids` **預設空陣列**（7.3：不自動選擇 Reference Space）。
`model` 選填，裸值，生效順序見〈選擇 model〉。

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
Git 查詢或檔案讀取失敗、逾時會送出 `CODE_READ_FAILED` SSE error（非串流狀態碼為 502），
不會呼叫 AI、送出 done 或保存新草稿。只有 `git grep` 正常結束且零命中才算成功空結果；
錯誤內容不包含原始 stderr 或本機 repo 絕對路徑。

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

目前進度（後端與前端分開寫——兩邊不同步，合在一句會讓人以為畫面已經做好）：

- **後端完成**：「我的工作 repo」登錄、**週檢視**（`GET /worklogs/week`）、**草稿產生**
  （`POST /worklogs/{date}/draft/stream`）、**單日讀寫**（`GET`／`PUT /worklogs/{date}`，
  `PUT` 是逐筆欄位更新）、**送出**（`POST /worklogs/{date}/submit`）。
  **搜尋**（`GET /worklogs/issue-search`，2026-10-01；早期規劃的路徑是
  `/zplanner/issues/search`，實作時放進 `/worklogs/` 底下與其他工時端點同組）。
  **尚未實作**：`GET /zplanner/permissions`（權限預檢目前只在草稿的 SSE `meta` 裡一次帶回）。
- **前端完成**：工時工作台的導航與路由骨架、「我的工作 repo」設定頁、週檢視；
  本任務完成：單日工作區的產生／分配／歸屬確認／送出。

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
  標示來源，絕不自動填**（ADR-0008 的唯一硬規則：記到哪張單一律由 Viewer 在確認
  畫面選）。⚠️ 只對**這個 repo 的 commit 切出來的段**生效，不套到別的 repo 或純 Chat 的段。猜錯的那次長得跟猜對的一模一樣，而 ZPlanner 的工時會進月結，
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

### `GET /api/v1/worklogs/issue-search`

需登入。`?q=<關鍵字或單號>`。在**全部可記工時（worklogable）**的 issue 裡搜尋，
結果依專案分組——草稿卡「記到哪張單」搜尋框用的。系統候選只是用一個關鍵字自動搜出來
的，純 Chat 的段經常一筆都沒有，Viewer 必須能自己搜。

```json
{ "q": "備份", "count": 2,
  "groups": [ { "project_id": 77, "project_key": "OPS",
                "issues": [ { "issue_id": 20, "issue_key": "OPS-7", "title": "設定備份",
                              "project_id": 77, "project_key": "OPS" } ] },
              { "project_id": 42, "project_key": "PEI",
                "issues": [ { "issue_id": 30, "issue_key": "PEI-300", "title": "備份排程",
                              "project_id": 42, "project_key": "PEI" } ] } ] }
```

- 組依 `project_key` 排序（`null` 排最後），組內依 `issue_key` 排序。`issues[]` 的五個
  欄位與 `candidates[]` 的前五個同形，直接拿去 `PUT` 的歸屬四欄。
- **沒有**信心度、理由與權限預檢：worklogable 本身就是「能不能填」的權威來源；逐專案的
  角色預檢是產草稿時的事（每專案 2 次 HTTP），打字時跑會慢到不能用。
- 路由宣告在 `GET /worklogs/{date}` 之前，否則 `issue-search` 會被當成日期。

| 情況 | 回應 |
| :--- | :--- |
| 🔴 `q` 省略、空字串或只有空白 | `400 INVALID_PARAMETER`，**不送 ZPlanner**——它對空字串回 0 筆而不是全部，照送會得到「沒有可記工時的單」這個錯誤結論。前端也在送出前擋下 |
| `q` 超過 100 字 | `400 INVALID_PARAMETER` |
| ZPlanner 沒設定（`ZPLANNER_BASE_URL`／`ZPLANNER_APIKEY`） | `500 CONFIGURATION_ERROR`，原因在 `message`。**不回空結果**：空的 `groups` 與「真的搜不到」長得一模一樣 |
| 連不上／逾時 | `502 ZPLANNER_UNAVAILABLE`（唯讀，可以直接重試） |
| ZPlanner 回了認不出 id 的項目 | `502 ZPLANNER_API_ERROR`。**不跳過那一筆**——靜默少一筆，Viewer 就會以為「沒有這張單」 |
| 搜不到 | `200`，`count: 0`、`groups: []` |

### `POST /api/v1/worklogs/{date}/draft/stream`

需登入。**SSE**。`{date}` 是 `YYYY-MM-DD`。body：
```json
{ "tz_offset": "+08:00", "repo_ids": [3], "space_ids": ["spaces/AAA"],
  "total_hours": 8, "provider": null }
```

| 欄位 | 語意 |
| :--- | :--- |
| `tz_offset` | **必填**，`+08:00`／`+0800`／`Z`。日界由它決定——「半夜一點的 commit 算哪一天」必須有寫得出來的依據，不是伺服器剛好設成什麼。非法值 `400` |
| `repo_ids` | 省略／空 ＝ 全部**啟用中**的工作 repo。指定了但那筆不存在或已停用 → `404 WORK_REPO_NOT_FOUND` |
| `space_ids` | 省略 ＝ 釘選的 Space ∪ 當天有 Mention 命中的 Space。**絕不掃全部 436 個**；非 `spaces/` 開頭 → `400` |
| `total_hours` | **省略就不分配**，`entries[].hours` 一律 `null` |
| `provider` | 這一階段**不接 AI**，只回進 `meta.provider` 備查 |

⚠️ **這裡沒有 `model` 欄位**，另外兩個串流端點有（見〈選擇 model〉）。理由同上：
這一階段不接 AI（`meta.model` 固定 `null`），收下一個保證無效的參數比不收更糟——
呼叫端會以為自己選的模型生效了。

事件序：`meta` → 每群一個 `chunk` → `done`。**事件型別不新增**，仍只有
`meta`／`chunk`／`done`／`error`。以上參數錯誤全部**在 SSE 開始之前**回 4xx。
⚠️ **2026-10-01 起沒有 `force` 欄位**（以前用來確認「重產會清掉已選的專案」）。重產對不上
新分段的已選單號照規格留空、不擋，事後列在 `done.attributions_cleared`。舊前端若還帶著
`force`，多出來的鍵被忽略——它本來就蓋不過「已送進 ZPlanner」那條線。

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
                  "source": null,
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
                  "adjustments": [] },
  "attributions_cleared": { "count": 1, "issue_keys": ["OPS-7"] } }
```

`attributions_cleared`：這次重產因分段改變而**清空**的已選歸屬——`count` 是段數，
`issue_keys` 是被清掉的單號（去重；沒有單號的不列但照樣計數）。第一次產生是
`{ "count": 0, "issue_keys": [] }`。**只在這次 done 出現、不落地**：它描述的是「這次重產
做了什麼」，存進 meta 的話 Viewer 重新選好之後畫面仍會一直說「已清空」。前端據此在
畫面上方顯示「有 N 段因分段改變，歸屬已清空：PEI-133、…」。

- 🔴 **`entries[].issue_id`／`issue_key` 不會是系統選的**——系統只給候選，選擇權在
  Viewer（ADR-0008 的唯一硬規則）。一份看起來合理的自動歸屬，代價由一個月後發現
  填錯的人支付（月結後不能直接改，要走 correction-request）。第一次產生一律是 `null`，
  落地的那一份也是空的；**重產時 Viewer 在同一段選過的值原樣保留**（那是人的決定，
  不是系統的猜測，見下方重產規則）。「上次記到」的記憶**只進候選**，不進這四欄。
  （`allocation.entries[].issue_key` 是**分配演算法的 tie-break 依據**，不是歸屬，
  前端不可拿它去預選。）
- `total_hours` 省略時 `hours`／`allocated_hours`／`allocation` 全部是 `null`——
  使用者還沒決定總時數之前，不該看到憑空冒出來的數字。
- 草稿**一天一份**（`UNIQUE(viewer_id, work_date)`），同一天再產一次是覆蓋——
  覆蓋是**整組 DELETE 再 INSERT**，所以要先決定舊的歸屬怎麼處理：

  | 情況 | 處理 |
  | :--- | :--- |
  | 有任何一筆送進 ZPlanner（`zplanner_worklog_id` 有值） | 串流開始前 `409 WORKLOG_ALREADY_SUBMITTED`，`detail: { submitted_count, force_allowed: false }`。**沒有任何參數蓋得過**：worklog id 弄丟就永遠對不回來，之後只剩 correction-request |
  | 已選好單的筆**對得到**這次的某一段 | 四欄原樣帶到新的那一筆 |
  | 已選好單的筆**對不到**這次的任何一段 | **照規格留空、不擋**，重產照常完成，列在 `done.attributions_cleared`（Viewer 決定：事後告知，不先確認） |

  **「對得到」的判準（兩個條件同時成立）**：(1) 舊 entry 用 `evidence_refs` 對回舊
  `meta.clusters[]` 的那一群，那一群的 `key`（`issue:PEI-133`／`repo:cs-backend`／
  `space:spaces/AAA`）也出現在這次的切段裡；(2) 舊 entry 的 `evidence_refs` 與新那一段的
  `evidence_refs` **至少有一筆交集**。
  🔴 只看 key 會錯配：repo 段的 key 是 `repo:<名稱>`、Space 段是整天的 `space:<id>`，都與內容
  無關。例：舊的 cs-backend 段含 commit A、B 且選了 OPS-7；重產時 A、B 改歸到 issue 段、新的
  commit C 落進 cs-backend 段——只看 key，OPS-7 就被貼到毫不相干的 C 上。要求交集之後這算
  「對不到」，留空並列出。不要求完全相等：重產多半是那天多了幾個 commit，refs 必然不同。
  ref 是 `commit:<sha>`／`chat:<message_name>`，重產前後穩定。
  時數、備註、送出狀態一律由新產生的值取代。判斷以**寫入前最新的草稿**為準：掃 git 那段
  時間 Viewer 若在另一個分頁又選了幾筆，以那時的狀態為準。
  ⚠️ 已送出的那道守門在串流開始前檢查一次，**並且在真正寫入的那個 transaction 裡
  再檢查一次**：中間隔著 git 掃描與 ZPlanner 呼叫（可達數十秒），這段時間內若有另一
  條路徑把工時送出去，只靠前面那次檢查會把剛拿到的 worklog id 靜默抹掉。串流已經
  開始時，這道守門會變成 `error` 事件（`code: "WORKLOG_ALREADY_SUBMITTED"`），**草稿
  一筆都不會被改到**（`meta`、`generated_at` 也不動）。
- 錯誤主體的 `error.detail` **只有拋的人刻意給結構化補充時才出現**，型別是物件。
  其餘情況（大多數錯誤）沒有這個欄位——字串型的 detail 是內部訊息，只進 log。
- 候選 issue 用 `list_worklogable_issues(q=群的關鍵字)` 撈，**`q` 必填**
  （ZPlanner 對空字串回 0 筆而不是全部）。`confidence` 只有三級並**一律附一句可見
  的理由**：不做排名徽章、不做百分比、不做進度條——要讓人讀理由而不是讀名次。
- `candidates[].source`：`issue_key`（commit／發言裡直接寫了單號，`strong`）、
  `default_project`（**這一段所屬 repo** 設定的預設 ZPlanner 專案底下的單，`medium`，
  理由會寫出是哪個 repo）、`search`（關鍵字搜到的，`weak`）、`last_used`（見下）。
  ⚠️ 預設專案加權**只比對這一段的 commit 所屬的 repo**（2026-10-01 以前比對的是所有
  啟用 repo 的聯集，理由文字卻寫「該 repo」）。純 Chat 的段沒有所屬 repo，不加權。
- **「上次記到」**：`clusters[].source` 有值時（整段的證據都來自同一個 Space 或同一個
  repo；跨來源的段是 `null`），若這位 Viewer 曾在這個來源選過單，該段候選的**第一項**是
  `{ …, "source": "last_used", "confidence": "medium", "reason": "「cs-backend」上次記到 PEI-133",
  "last_used_at": "…", "matched_source": null }`。**同一張單這次也被搜到時合併成一筆**：
  仍排第一、`source` 仍是 `last_used`，但 `confidence` 取搜到那一邊的（commit 直接寫了單號
  就是 `strong`，不被記憶的 `medium` 降級）、`reason` 兩句都留（以「；」相接）、
  `matched_source` 是搜到的來源（`issue_key`／`default_project`／`search`）、`title` 借搜到的
  那份。沒搜到時 `matched_source: null`、`title` 是空字串（記憶不存標題）。ZPlanner 不可用或那一段查詢失敗時**照樣有**（記憶是本地資料），
  此時 `can_worklog: null`、`permission_checked: false`。🔴 **它只是候選**，四個歸屬欄位
  照樣是 `null`，Viewer 點了才算數。
- `clusters[].source`：`{ "kind": "repo" | "space", "id": "<repo 絕對路徑或 space_id>",
  "label": "<repo 名或 Space 名>" }` 或 `null`。PUT 選定歸屬時靠它知道要記到哪個來源上。
- **三種「沒有系統候選」要分開讀**：`zplanner.available: false`（＋`reason`）＝不可用；
  `zplanner.errors[]` 裡有 `cluster` 等於這一段的 `key` ＝那次查詢失敗（帶 `term`，可重試）；
  兩者皆非而系統候選是空的 ＝有查但沒命中（`last_used` 不算系統候選，除非它帶著
  `matched_source`——那代表同一張單這次也被搜到了）。
- 權限預檢用 `list_project_role_permissions` ＋ `list_project_members`。
  查不到就 `can_worklog: null` ＋ `permission_checked: false`，**不假裝有權限**。
  ⚠️ `meta.zplanner.permission_checked` 現階段**一律是 `false`**：比對用的是登入的
  Google 信箱，而 `members[].user_email` 未必是同一個帳號；要確定得先有
  `preferences.zplanner_email`（該欄位尚未存在）。
- 一次最多預檢 5 個專案。每群最多 3 個**系統候選**；有「上次記到」時它另外排在最前面，
  所以**最多 4 項**（記憶與系統候選是同一張單時合併成一筆，不會重複佔位）。清單永不封閉，
  其餘靠搜尋框（`GET /worklogs/issue-search`）。
- **ZPlanner 不可用時照樣產得出草稿**：`meta.zplanner.available` 為 `false`、
  候選只剩「上次記到」（沒有記憶就是空陣列），其餘一切照常。

`error`：沒有任何 commit／發言時發 **`NO_EVIDENCE`**（SSE-only code，比照
`NO_MESSAGES`），**且不落地任何草稿**——硬產一份空草稿會讓使用者把時數分給不存在
的證據。其餘串流中的錯誤照 8.3 轉成 `error` 事件。

### `GET /api/v1/worklogs/{date}`

需登入。`{date}` 是 `YYYY-MM-DD`。讀這一天的草稿，**含 Viewer 存過的歸屬與時數**
（重新整理頁面後就靠這支把畫面畫回來）。回應是**扁平的草稿物件**（不包外層）：

```json
{ "id": 7, "work_date": "2026-09-24", "status": "draft", "total_hours": "8.00",
  "tz_offset": "+08:00", "generation_meta": { "clusters": [ "…同 SSE meta…" ] },
  "generated_at": "…", "created_at": "…", "updated_at": "…",
  "entries": [ { "id": 12, "draft_id": 7, "seq": 1,
                 "issue_id": null, "issue_key": null, "project_id": null, "project_key": null,
                 "hours": "3.50", "note": "…", "allocated_hours": "3.50", "weight": 0.42,
                 "rounded_up": false, "evidence_refs": ["commit:ab12…"], "candidates": [],
                 "state": "pending", "zplanner_worklog_id": null, "zplanner_issue_key": null,
                 "is_locked": false, "submitted_at": null,
                 "last_error_code": null, "last_error_message": null,
                 "created_at": "…", "updated_at": "…",
                 "cluster_key": "issue:PEI-133", "label": "…", "reason": "…" } ] }
```

- `entries[]` 是資料庫裡的完整明細（與 submit 回應的 `entries[]` 同一組欄位），
  **另補** `cluster_key`／`label`／`reason`。那三個不存在 DB，是用這筆的
  `evidence_refs` 以**集合相等**去對 `generation_meta.clusters[]` 取來的。
  ⚠️ **對不到就是 `null`，不會退回用 `seq` 位置去對**：重產時 meta 與 entries 分兩個
  交易寫，後者被已送出守門拒絕時 meta 已經換成新的一批，用位置對會把新群的
  說明靜默貼到舊的卡片上。前端看到 `null` 就畫「說明對不上」，不要自己補。
- 與 SSE `done` 的 `entries[]` **不是同一個形狀**：這裡多了送出結果的欄位
  （`zplanner_worklog_id`／`is_locked`／`last_error_*`／`submitted_at`），畫送出狀態要用。

| 情況 | 回應 |
| :--- | :--- |
| 這一天沒有草稿 | `404 WORKLOG_DRAFT_NOT_FOUND`（前端畫「還沒產草稿」的空狀態） |
| `{date}` 格式不對 | `400 INVALID_PARAMETER` |

### `PUT /api/v1/worklogs/{date}`

需登入。**逐筆欄位更新**，只改時數、「記到哪張單」（歸屬四欄，舊稱「專案歸屬」）、備註。🔴 **不是整組取代**：整組取代
要前端自己組出每筆的完整欄位，漏帶一個 `state`／`zplanner_worklog_id` 就是一次
資料遺失。也**不能新增或刪除** entry——entry 只能由草稿產生流程依證據造出來。body：

```json
{ "entries": [
    { "id": 12, "hours": "2.50",
      "issue_id": 4821, "issue_key": "PEI-133", "project_id": 42, "project_key": "PEI" },
    { "id": 13, "note": null } ] }
```

| 欄位 | 語意 |
| :--- | :--- |
| `id` | 必填，要改的那一筆。必須屬於這一天的草稿 |
| 其餘欄位 | **鍵沒帶 ＝ 不改；帶 `null` ＝ 清除。** 沒出現在這張表裡的鍵（`state` 等）一律 `400`，不是靜默忽略——靜默忽略的話前端以為改到了，實際上什麼都沒發生 |
| `hours` | 字串或 `null`。格式規則與送進 ZPlanner 的是**同一份**（最多三位整數、兩位小數，可為負數）；**不卡 0.5 倍數**，那是分配演算法的產出規則，不是 ZPlanner 的輸入限制。收到數字型別 → `400`。**不含任何數字的字串**（`""`、`"."`、`"-"`、`"-."`）也是 `400`——要清除時數請送 `null`，不要送空字串。前後空白會被去掉 |
| `issue_id`／`issue_key`／`project_id`／`project_key` | **四個要一起帶**，而且 `issue_id` 與 `issue_key` 要同為 `null` 或同有值。只改一半的話，送出閘（看 `issue_id`）與重產守門（看 `issue_id` 或 `issue_key`）對「這筆指派了沒」會給出不同答案。**不限定只能選 `candidates` 裡的**（`GET /worklogs/issue-search` 搜到的照樣可以） |
| `note` | 字串或 `null` |

回應：`200`，與 `GET` **同一個形狀**（整份草稿寫完後重讀）。前端拿它直接重畫。

- 🔴 **整批全有或全無。** 任何一筆被拒絕，這次請求**一筆都不會改**（守門與寫入在
  同一個 DB 交易裡）。
- **已送出守門是逐筆的：只看這次要改的筆。** 要改的筆裡有 `zplanner_worklog_id`
  有值者才 `409`；`failed`／`unknown`（沒有遠端 id）可以改。當天別處有筆已經送出，
  **不影響**其他筆的修改——否則選錯專案而 `failed` 的那筆就沒有路可以修。
- **選定一張單（`issue_id` 有值）時順手記住「這個來源上次記到哪張單」**：來源取自
  這筆所屬那一群的 `generation_meta.clusters[].source`；對不到群、或那群的 `source` 是
  `null`（跨來源的段、2026-10-01 以前產的草稿）就不記。清除歸屬（四欄 `null`）**不會**
  蓋掉上一次的記憶。記憶與歸屬寫在**同一個交易**：整批被拒絕時記憶也不動。
- 未觸碰的欄位（`state`／`zplanner_worklog_id`／`is_locked`／`last_error_*`／
  `submitted_at`）一律原樣保留，改完 `failed` 那筆的歸屬，它仍然是 `failed`，
  上一次的錯誤訊息也還在，重打一次才會更新。
- 與送出**搶同一把鎖**：同一天正在送出時 PUT 回 `409 WORKLOG_SUBMIT_IN_PROGRESS`。
  送出流程在迴圈開始前就把 entries 拍下來了，送出中被改值的話 ZPlanner 收的是
  舊值、本地卻存著新值。⚠️ 反過來也成立：PUT 進行中按送出，也會拿到「正在送出中」
  （文字不精確，是已知限制）。

| 情況 | 回應 |
| :--- | :--- |
| 這一天沒有草稿 | `404 WORKLOG_DRAFT_NOT_FOUND` |
| 有 `id` 不屬於這份草稿 | `400 INVALID_PARAMETER`，`detail: { unknown_entry_ids, draft_entry_ids }`（鍵名同 submit）。**是 400 不是 404**，理由同 submit |
| **要改的筆中有已送出者** | `409 WORKLOG_ALREADY_SUBMITTED`，`detail: { submitted_count, force_allowed: false }`。`submitted_count` 是**這次要改的筆裡**已送出的筆數，不是整份草稿的。已送出的工時要改只能走 correction-request |
| 同一天的送出（或另一個 PUT）正在進行中 | `409 WORKLOG_SUBMIT_IN_PROGRESS`，`detail: { work_date }` |
| `entries` 是空陣列、同一個 `id` 出現兩次、歸屬四欄沒一起帶、`issue_id`／`issue_key` 一個有值一個 `null`、`hours` 格式不對或不是字串、帶了不認得的鍵 | `400 INVALID_PARAMETER`（**沒有 `detail`**，原因在 `message`） |
| `{date}` 格式不對 | `400 INVALID_PARAMETER` |

### `POST /api/v1/worklogs/{date}/submit`

需登入。`{date}` 是 `YYYY-MM-DD`。把那一天的草稿**逐筆**寫進 ZPlanner。body：
```json
{ "entry_ids": [12, 13] }
```

| 欄位 | 語意 |
| :--- | :--- |
| `entry_ids` | **省略 ＝ 送整天**。給了就只處理那幾筆——`failed`／`unknown` 的單筆重打走的是這同一支端點，不另開路徑。⚠️ 帶了它的時候，「有筆沒選專案就擋下整批」那道閘**只檢查選中的那幾筆**（否則當天別處只要有一筆沒選專案，`failed` 的那筆就永遠重打不了）。空陣列 `[]` 回 `400`：送 0 筆卻回成功，分不出是前端漏帶 id 還是真的沒東西要送 |

```json
{ "work_date": "2026-09-13", "status": "partial",
  "entries": [ { "id": 12, "seq": 1, "issue_id": 4821, "issue_key": "PEI-133",
                 "hours": "3.50", "note": "…", "state": "submitted",
                 "zplanner_worklog_id": 116847, "zplanner_issue_key": "PEI-133",
                 "is_locked": false, "submitted_at": "…",
                 "last_error_code": null, "last_error_message": null,
                 "...": "（其餘欄位同草稿的 entries[]）" } ],
  "summary": { "submitted_count": 3, "skipped_count": 1,
               "failed_count": 1, "unknown_count": 0,
               "skipped_duplicates": [
                 { "entry_id": 15, "seq": 4, "issue_key": "PEI-140", "hours": "1.00",
                   "existing": { "hours": "1.0", "note": "…", "issue_title": "…" },
                   "reason": "…可直接顯示的繁中句子…" } ],
               "orphans": [], "not_attempted_count": 0 } }
```

- 🔴 **HTTP 200 不代表每一筆都送成功。** 只要這一批開始送了，回的就是 200，
  逐筆成敗看 `entries[].state` 與 `summary`。用 4xx 表達「有筆失敗」會讓前端
  顯示成「整批沒送出」，使用者於是再按一次送出鈕——ZPlanner 那側已經成功的
  那幾筆就變成重複工時，而**重複工時在月結之後刪不掉**（只能走
  correction-request）。**也刻意不用 207 Multi-Status**：既有錯誤契約沒有這一類。
  錯誤信封只留給**一筆都沒送**的整批拒絕（見下方錯誤表）。
- `entries[]` 是**寫完之後重讀**的完整草稿明細，形狀與草稿的 `entries[]` 相同
  （多出來的欄位本來就在：`state`／`zplanner_worklog_id`／`zplanner_issue_key`／
  `is_locked`／`submitted_at`／`last_error_code`／`last_error_message`）。
  前端拿它直接重畫那一天，不必自己合併。
- 🔴 **`summary` 的四個計數描述的是「重讀之後整份草稿」，不是「這一次送了幾筆」。**
  單筆重打時 `submitted_count` 會包含上次就已經成功的那幾筆——它與 `entries[]`
  是同一份帳，也與週檢視的 `submitted_count`／`failed_count`／`unknown_count`
  是同一套鍵名，兩個畫面才講同一套話。
- `status` 是重算後的草稿狀態，同時會寫回 DB：**每一筆都 `submitted` 或
  `skipped`** 才變成 `submitted`，完全沒有任何一筆送成功維持 `draft`，其餘是
  `partial`。少了這一步，週檢視的 `totals.days_submitted` 會永遠是 0。
- **逐筆一交易，每成功一筆立刻落 DB**，不等整批跑完。所以跑到一半的草稿是
  混合狀態（同一份草稿裡 `submitted`／`failed`／`unknown` 並存），前端必須畫得
  出來。不這樣做的話，第 N 筆失敗時前面幾筆的 `zplanner_worklog_id` 就沒落地，
  ZPlanner 那側的工時變成沒有人認領的孤兒，下次重送會再建一次。
- 🔴 **有任何一筆送不出去就一筆都不送**（沒選專案、或沒有時數），回 `400`。
  兩種筆送到 ZPlanner 之前都會先被 client 的參數檢查擋下，所以**不會**造成重複
  工時——擋在前面是為了不要**部分送出**：那天的工時會變成不完整，而畫面上沒有
  任何訊號說明少了什麼。
  ⚠️ 沒有時數不是罕見的邊界情況：產草稿時省略 `total_hours` 就不分配，那時候
  **整份草稿的 `hours` 一律是 `null`**——那是正常流程的中間狀態，不是壞掉的資料。
- **已經有 `zplanner_worklog_id` 的筆直接跳過，不重打。** 那是「這筆送過了」的
  唯一憑據。
- **送出前先查 ZPlanner 這一天已經有哪些工時**（`GET /api/worklogs/export/`，
  `date_from` ＝ `date_to` ＝ 這一天，**整天只查一次、不逐筆查**）。同一個 issue
  ＋ 同樣時數已經存在 → 這筆標 `state: "skipped"`，並列進
  `summary.skipped_duplicates`。
  - 比對 **issue ＋ 時數，不比對 note**：note 會被使用者編輯過，比對它會漏判而
    造出重複工時。時數兩側正規化到同一刻度後再比（`"2"` 與 `"2.0"` 算同一個）。
  - ⚠️ 代價是會誤殺「同一天、同一 issue、同樣時數」的第二筆合法工時（罕見但
    存在）。取捨是寧可漏送也不要重複——**所以被跳過的筆一定看得見**，
    `skipped_duplicates` 是它唯一的出口。`existing` 是比對到的那一列遠端工時原文。
  - 🔴 `state: "skipped"` 的筆**不會**把比對到的遠端 id 寫進 `zplanner_worklog_id`：
    那筆不是我們建的，認領它會讓上面說的誤殺變成永久而且再也看不見。
  - 🔴 **這支查詢失敗 ＝ 整批中止**（回錯誤信封，一筆都不送）。不准「查不到就
    當作沒有重複、照送」——那等於放棄唯一的去重依據，而重送的代價不可逆。
- **`unknown` 不是「失敗」，是「不知道送進去沒有」**（`ZPLANNER_WRITE_INDETERMINATE`：
  請求在傳輸層就斷了，連「連線被拒」也算，因為分不出是送達前還是送達後斷的）。
  這種筆**不會被自動重試**，要先用 ZPlanner 的匯出查那一天對帳再決定。
  ⚠️ ZPlanner 回了成功、但回應主體裡沒有 worklog id 時**也算 `unknown`**（不是
  `failed`）：那筆工時很可能已經建好了，標成 `failed` 會讓重打再建一次。
- `summary.orphans` 是「工時已經寫進 ZPlanner，但要寫回草稿時，本地那一筆已經
  不符合寫回條件了」。裡面帶著 `zplanner_worklog_id`——那是唯一還找得到那筆遠端
  工時的線索，要改要刪都只能直接去 ZPlanner。正常情況這個陣列是空的。
  - 🔴 **`reason` 不預設成因**，另附 `possible_causes` 陣列把所有可能列出來：
    同一天另一個送出同時在跑、送出期間草稿被重新產生、那一筆
    被其他路徑改成別的狀態。這一層分不出是哪一種（寫回用的 `UPDATE` 只回「影響
    0 列」，不回為什麼）。**寫死一個成因的代價不是話講得不夠準**——人照那句去查
    重產紀錄卻查不到東西，就會把一筆真實存在的遠端工時當成誤報放掉，而它每個月
    都會出現在報表上。
- `is_locked` 直接抄 `POST /api/worklogs/` 回應的欄位。「送出時沒鎖、之後月結才
  鎖」這支端點看不到，那要靠對帳補。
- 🔴 **同一天同時只准有一個送出在跑**，第二個**立刻**回 `409`
  （`WORKLOG_SUBMIT_IN_PROGRESS`，**不排隊等**），一筆都不送。兩個重疊的送出會
  各自查一次「這天 ZPlanner 已經有哪些工時」當去重底稿，而兩份底稿都是在對方
  寫入之前拍的，於是去重對兩邊都說「沒有重複」——同一筆被送兩次。
  2026-09-14 實測（兩條執行緒相隔 20ms、草稿 2 筆）：`create_worklog` 被呼叫
  **4 次**，而先回的那個請求回報 `submitted=2 failed=0 unknown=0 orphans=0`，
  **重複工時在它身上完全隱形**。
  - 不排隊等是刻意的：排隊會讓第二個請求最長卡一整個時間預算（90 秒），而使用者
    等不到回應時最常做的事就是再按一次——要防的動作被原地放大。前端收到 409 應該
    顯示「正在送出中，等它跑完」，**不要自動重試**。
  - ⚠️ **已知邊界：這道防護是行程內的鎖，只在單 worker 部署下有效。** 部署模型是
    一人一部署、單 worker uvicorn，所以涵蓋得住；**多 worker／多行程時兩個請求會
    各自拿到自己行程的鎖，防護等於不存在**，那時必須改成 DB 認領。這是刻意的取捨
    （DB 認領會新增「行程崩潰讓那些筆永久卡在認領中」這個失敗模式，需要另做回收
    機制），不是疏漏。啟動時若偵測到 `--workers`／`WEB_CONCURRENCY` 大於 1，log
    會留一行 warning——**那只是提醒不是防護**，偵測不到不代表安全。
- 🔴 **整批有 90 秒的時間預算**（`WORKLOG_SUBMIT_BUDGET_SECONDS`）。到點就**不再
  開始新的一筆**，停送的筆維持 `pending`、**仍然回 200**，筆數放在
  `summary.not_attempted_count`（整數，**一律都在，即使是 0**）。
  - 到點是「不開始下一筆」，**不是砍掉進行中的那一筆**：砍掉一個已經發出去的寫入
    只會得到 `unknown`（不知道寫進去沒有、不准自動重試、要人工對帳），比乾脆沒送
    更糟。
  - ⚠️ **所以整支端點的實際上限不是 90 秒。** 最後一筆可以在 t=89.9s 才起跑，再吃
    一次 client 的 `(5, 20)`——正常情況約兩分鐘封頂，對方慢速吐 chunk 時更久。
    前端設 HTTP timeout 與「請稍候」文案時用這個數字，不要用 90。
  - ⚠️ 停送的筆在 DB 裡與「從未送過」長得一樣（都是 `pending`），
    `not_attempted_count` 是使用者唯一看得出「這批沒跑完」的訊號。前端要提示他
    再按一次把剩下的送完。
  - 為什麼不靠 client 的 timeout：`(5, 20)` 是 connect 與**每一次 read** 的上限，
    不是單次請求的總長，更不是整批的總長——對方慢速吐 chunk 時單次請求可以無上限
    地拖，N=10 的一批最壞約 275 秒。**也不准改用縮短 read timeout 來達成**：那會
    讓首 byte 比較慢的正常請求變成 `unknown`。

整批拒絕（**一筆都沒送**，回 8.4 的錯誤信封）：

| 情況 | 回應 |
| :--- | :--- |
| 這一天沒有草稿 | `404 WORKLOG_DRAFT_NOT_FOUND` |
| **這一天的送出正在進行中** | `409 WORKLOG_SUBMIT_IN_PROGRESS`，`detail: { work_date }`。**立刻回，不排隊等。** 🔴 刻意與 `WORKLOG_ALREADY_SUBMITTED` 分開，雖然都是 409：那個是「這份草稿已經送過了」（終局狀態，去看結果或走 correction-request），這個是「正在送、還沒結束」（暫時狀態，等一下再試）。使用者的下一步不同，共用一個碼前端只能猜一個 |
| **有筆送不出去**：沒選 ZPlanner 專案、或沒有時數（帶 `entry_ids` 時只看選中的那幾筆） | `400 INVALID_PARAMETER`，`detail: { unassigned_count, unassigned_entries: [{ entry_id, seq }], missing_hours_count, missing_hours_entries: [{ entry_id, seq }] }`。**四個鍵一律都在，即使是 0**。兩種分開數、分開講——使用者的下一步不同（一個去選專案，一個去輸入總時數）。未指派的判準是 `issue_id`（`create_worklog` 吃的是它），不是給人看的 `issue_key`；`seq` 帶著，人才找得到該去補畫面上的第幾張卡 |
| `entry_ids` 指到不屬於這份草稿的 id | `400 INVALID_PARAMETER`，`detail: { unknown_entry_ids, draft_entry_ids }`。**是 400 不是 404**——404 的語意是「這天還沒有草稿」，前端收到它會去畫空狀態，但草稿明明在，錯的是送過來的 id |
| `entry_ids` 是空陣列 | `400 INVALID_PARAMETER` |
| `{date}` 格式不對 | `400 INVALID_PARAMETER` |
| 沒設定 ZPlanner（`ZPLANNER_BASE_URL`／`APIKEY`） | `500 CONFIGURATION_ERROR`。⚠️ 與週檢視不同：那支沒有 ZPlanner 照樣回本地草稿，這支整個目的就是對 ZPlanner 寫入，降級之後一件事都做不成 |
| 查「這一天已有哪些工時」失敗 | 照 client 的錯誤原樣往上（`502 ZPLANNER_UNAVAILABLE`／`ZPLANNER_AUTH_ERROR`／`ZPLANNER_API_ERROR`…） |
| 某筆選了 `issue_id` 卻沒有 `issue_key` | `400 INVALID_PARAMETER`。少了 key 就沒有東西可以跟遠端對，等於完全沒有去重 |
| 遠端某列的 issue 正是要送的那個，但它的時數解析不出來 | `502 ZPLANNER_API_ERROR`。判不出時數就判不出是不是重複，而「判不出就當沒有」正是要防的不可逆錯誤 |

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
   API 串接、有自己的畫面。目前進度——**後端**：ZPlanner client 層、「我的工作
   repo」登錄端點、週檢視、草稿產生（SSE）、單日讀取與**逐筆欄位更新**
   （`GET`／`PUT /worklogs/{date}`；⚠️ `PUT` **不是整組取代**，只送改過的那幾筆、
   那幾個欄位）、送出（`POST /worklogs/{date}/submit`）。**前端**：設定頁、工時
   工作台的導航與路由骨架、週檢視；本任務完成：單日工作區的產生／分配／歸屬確認／送出。
   ⚠️ 送出那支的畫面要記得：**HTTP 200 不代表每一筆都成功**，逐筆狀態在
   `entries[].state`；跑到一半的草稿是混合狀態，前端必須畫得出來。另外兩個：
   收到 `409 WORKLOG_SUBMIT_IN_PROGRESS` 要顯示「正在送出中」並**停止自動重試**；
   `summary.not_attempted_count > 0` 代表整批超時停送，要提示使用者再按一次把
   剩下的送完。）
4. Action Items 萃取：從摘要 Markdown 中抓 `• [負責人] 任務` 這種行，轉為可勾選項目，
   支援「複製為 Markdown」。
5. 空間清單有 436 個，列表需要虛擬滾動或分頁，並提供名稱搜尋。

## 專案脈絡（選用功能）

伺服器預設關閉。設定 `CHATPULSE_PROJECT_CONTEXT_ENABLED=true` 後，`GET /api/v1/me` 增加可選的 `features.project_context`，偏好增加 `project_context_opt_in`，預設為 false。Viewer 必須先以 `PATCH /api/v1/preferences/project-context` 傳送 `{"enabled":true}`。功能關閉時前端不呼叫專案 API。

- `GET /api/v1/project-profiles` 回傳 `{"profiles":[...]}`，僅含當前 Viewer 的 ProjectProfile。
- `POST /api/v1/project-profiles` 建立專案，body 為 `name` 與 `sources`。
- `PUT /api/v1/project-profiles/{id}` 整份替換，必須帶原 `revision`。CAS 衝突或專案不存在回 409，來源錯誤或重複名稱回 4xx，全部回滾。
- 專案最多 12 筆來源，至少 1 筆。每題最多採用既有 `CODE_MAX_PROJECTS_PER_DRAFT` 筆 Git，超量拒絕，不截斷。

來源是封閉聯集，不接受 owner、repo_path 或任意 URL。

```json
{
  "name": "範例專案",
  "sources": [
    {"id":"pm","kind":"chat","label":"PM","space_id":"spaces/fake-pm","category":"pm"},
    {"id":"git","kind":"git","label":"程式碼","code_project_id":1,"environment":"production","paths":["src/main.py"]}
  ]
}
```

Chat 的 category 為 `space|pm`，關聯只使用完整 Space ID。Git 必須是同一 Viewer 的既有啟用登錄，environment 為 `production|uat|dev`，paths 只能是 repo 內相對路徑。綁定中的 Git 登錄不可刪除，必須先更新專案來源。

`POST /api/v1/mentions/{id}/draft/stream` 可帶 `project_selection`。本模式僅支援單一 Mention，拒絕非空的 `merge_mention_ids`、`reference_space_ids` 或 `code_refs`。未提供 project_selection 的既有流程保持相容。

```json
{
  "project_selection": {
    "profile_id": 1,
    "revision": 1,
    "sources": [
      {"binding_id":"pm","use":"metadata_only"},
      {"binding_id":"git","use":"reply_context"}
    ],
    "target_space_id":"spaces/fake-target",
    "target_thread_name":"spaces/fake-target/threads/fake"
  },
  "limit":50,
  "code_terms":["known_symbol"]
}
```

sources 必須完整列出該版本所有 binding，各自只能選 `metadata_only|reply_context`。所有來源預設 metadata_only。確認目標必須與 Mention 的 Space/thread 完全一致，包括無 thread 時的 null。metadata_only 不讀 Chat/Git 內容，也不將來源關聯或內容放入 prompt。本題明選的專案名稱與核准 Git 環境會作為問題範圍提示，仍不保證能從同一 PM Space 自動分離不同專案的對話。讀取資格由 Viewer 本人的 OAuth 與 Google 決定。

SSE meta 增加 `project_scope` 與 `semantic_status:"unverified"`。scope 保存 owner、Mention/message、確切 target、專案名稱與 revision、來源與用途、Git registration fingerprint/resolved branch、limit、terms、schema_version 與 digest。digest 只對應設定，不是授權或事實支持。Git 實際 commit SHA 沿用 code_refs 實讀記錄，不納入登錄設定 fingerprint。新模式 done 增加 `scope_digest` 與 `project_status:"complete"`。EOF、error 或 abort 都不是完整專案草稿。

草稿 generation_config 保存原始 meta、`project_status:"complete|partial"` 與 `semantic_status:"unverified"`。`GET /mentions/{id}/draft` 增加 `scope_state:"current|stale"|null`。還原顯示歷史 snapshot，不以目前專案重建，也不恢復本題來源核准或目標同意。專案修改或 Git 讀取設定變更會使歷史 scope stale。實讀欄位只代表已回報的讀取資訊，不表示每個來源都完整支持答案。

專案草稿送出至 `POST /mentions/{id}/reply` 必須同時帶 `draft_id` 與 `scope_digest`。伺服器在任何 Google client 呼叫之前，以持久化草稿驗證 Viewer、Mention、target、完整狀態、digest、專案 revision 與 Git fingerprint。失效、部分草稿、缺 digest、跨 Mention/Viewer 或功能後來關閉，均拒絕，不降級成一般草稿。人工修改文字仍未語意查證。既有未附 scope 的草稿與人工回話保留，送出仍須人工二次確認。
