# 交接：接續 ChatPulse 的下一個 session

> 寫於 2026-09-05。這份是給「沒有前一個 session 記憶」的人／AI 看的。
> 只寫**接手時真的需要知道的事**，其餘一律指向對應文件。

---

## 直接可用的提示詞

把下面整段貼進新 session 即可：

```
接手 /Users/cheng/google-chat-bot（ChatPulse — Google Chat 摘要與 Mention 收件匣）。

先讀這四份，順序不要換：
1. docs/HANDOFF.md   ← 交接重點與已知的坑（就是這份）
2. SPECIFICATION.md  ← 需求規格 v2.0，驗收條件在第十三節
3. docs/verification-log.md ← 哪些宣稱有證據、證據強度分三級
4. docs/api-contract.md     ← 前後端 API 契約

規格書的 Phase 0/1/2 已全部完成並驗證過，main 分支是可運作的狀態。
不要重做已完成的部分；要動之前先跑一次驗證確認現況。

我接下來想做的是：〈在這裡寫你要做的事〉
```

---

## 30 秒現況

- **狀態**：規格書 v2.0 的 Phase 0／1／2 全部完成，21 條驗收條件逐條有證據
- **分支**：`main`。2026-09-05 的工作共 13 個 commit
- **服務**：`./scripts/start-web.sh` → http://localhost:8000
- **AI 供應商**：兩個實作——`claude_cli`（吃本機 Claude Code 訂閱，零設定）與
  `gemini`（每天 20 次）；另有 `claude`／`auto` 兩個別名，預設 `claude`。
  `claude_api`（Anthropic API）已於 2026-09-05 移除，理由見 SPECIFICATION.md 3.2

---

## 先跑這個確認現況（不消耗任何 AI 配額）

```bash
cd /Users/cheng/google-chat-bot
./scripts/start-web.sh          # 另開一個終端

export CHATPULSE_BOOTSTRAP_USER_ID=users/109827265019732088641
.venv/bin/python tests/e2e/check_stored_evidence.py   # 從資料庫重查宣稱
.venv/bin/python tests/e2e/test_providers.py          # AI 供應商切換
.venv/bin/python tests/e2e/test_static.py             # 靜態托管、授權迴歸
.venv/bin/python tests/e2e/test_add_annotation.py     # Mention 判定條件
npm --prefix dashboard/frontend run test              # 前端 31 項
```

五套全綠代表現況正常。要跑會消耗 AI 配額的完整套件：`tests/e2e/run_all.py`。

---

## 未完成 / 開放中的事

| 項目 | 狀態 | 下一步 |
| :--- | :--- | :--- |
| ~~**推送到遠端**~~ **已完成** | 2026-09-05 確認 `main` 與 `origin/main` 同步，那 13 個 commit 已推上去 | — |
| ~~**`claude_api` 供應商從未對真實 API 跑過**~~ **已消解** | 2026-09-05 決定**移除**這個供應商而不是留著等金鑰。未驗證的分支不該留在契約與前端選單裡 | 實作留在 git 歷史（`core/providers/claude_api.py`）；要接回來見 SPECIFICATION.md 3.2 的說明 |
| **「兩位真人 Viewer 各自 OAuth」未驗** | 只有一個 Google 帳號。授權隔離邏輯已用「資料庫建第二位 viewer + session」走 HTTP 層驗過 | 找同事用他的帳號跑一次登入 |
| **D-2：建議更換 Gemini API key** | 程式面已修（無硬編碼）。金鑰本身建議換 | 理由見 SPECIFICATION.md 3.3「憑證絕不進 repo」下方的收斂結論 |
| **`docs/index.html` 系統手冊** | 只修了過時的 MCP 路徑與則數預設值 | 尚未針對儀表板的 Phase 2 功能與供應商選擇改寫 |
| **R-2 定價未逐項核對** | 記錄機制完成（`token_usage` ＋ `GET /api/v1/usage`） | 累積兩週用量後再評估 |

---

## 會浪費你時間的坑（都是實測踩過的）

### AI 與配額

- **Gemini 免費層每天只有 20 次請求**（`gemini-3.6-flash`）。配額是
  **每個模型獨立**（quotaId 寫 `…PerProjectPerModel…`）——3.6 用完時 3.7 還能用。
  換模型：`CHATPULSE_GEMINI_MODEL=gemini-3.7-flash`
- **`maxOutputTokens` 包含 thinking token**。這是 D-3 的真正根因，而且**思考量
  因模型、甚至因每次請求而異**（實測 0～2,772）。所以 2048 不是「一定不夠」，
  是時好時壞、壞的時候**安靜截斷不報錯**
- **Claude Code CLI 預設很貴**：會載入 Claude Code 的 system prompt、CLAUDE.md
  與全部工具定義，一個 2-token 的 prompt 也會寫 19,085 token 快取。
  停用工具＋`--system-prompt` 後降到近 0（`core/providers/claude_cli.py` 已固定帶上）
- **不要用 `claude --bare`**：它只讀 `ANTHROPIC_API_KEY`、不讀 OAuth，會讓訂閱認證失效

### Google Chat API

- **`spaces.messages.list` 預設排序是 `createTime ASC`**（最舊優先）。不帶 `orderBy`
  抓「最近 N 則」會拿到最舊的 N 則——這是 D-6，修復前的摘要都在摘三年前的對話
- **使用者驗證下不回傳 `sender.displayName`**，所有發言者會變「未知成員」（D-7）。
  解法是從 `USER_MENTION` annotation 的 `startIndex`／`length` 反建名錄（`core/directory.py`）
- **`spaces/-/messages:search` 在此帳號回 200 但恆 0 筆**，連正對照都搜不到。
  採集器因此走輪詢（實作 B）。詳見 `docs/R1-findings.md`
- `messages.list` 的 filter 只吃 `createTime >`（`>=` 回 400）；search 端點則
  **完全不吃 createTime**

### 這台機器

- **zsh 有 `noclobber`**：`cmd > file` 會失敗，要用 `>|`
- **`gitstatusd` 會製造殘留的 `.git/index.lock`**：git 寫入指令不要接管線
  （`| tail` 會吞掉 exit code），開完分支要用 `git rev-parse HEAD` 比對基準
- **PreToolUse hook** 會攔截遞迴刪除與推送到 main，且是對**整條指令文字**比對——
  commit message 裡提到危險指令的字樣也會被擋
- 測試報告寫**兩份**（帶時間戳的那份不會被覆蓋）。這是因為曾經被配額耗盡的
  重跑蓋掉含真實數字的報告，導致宣稱變成查無來源

---

## 專案結構

```
core/            共用封裝（Google Chat／AI 供應商／SQLite／加密／名錄／採集器）
  providers/     AI 供應商：base 介面 ＋ claude_cli／gemini 兩個實作
mcp_app/         MCP 入口（5 個工具）、CLI 摘要、OAuth 授權精靈
dashboard/       FastAPI 後端 ＋ React 19 前端
tests/e2e/       七套端對端測試，對真實 API 取證、不用 mock
docs/            api-contract.md（契約）、R1-findings.md（R-1 實測）、
                 verification-log.md（證據等級）、adr/（架構決策）
config/ data/    憑證與 SQLite，皆不進版控
```

---

## 硬性限制（沿用自上一個 session）

**發送 Google Chat 訊息只能發到 `spaces/AAAAxLxqJxY`（0.暫存）。** 其他 Space 只能讀。
`tests/e2e/e2e_lib.py` 的 `guard_space()` 是硬性 assert，會擋掉任何其他目標——
不要為了方便把它改掉。
