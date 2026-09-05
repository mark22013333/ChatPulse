# ChatPulse — Google Chat 摘要與 Mention 收件匣

給工程團隊用的 Google Chat 輔助工具：查閱自己加入的 **Space**、產出結構化 **Summary**，
並在被人 @ 時取得一份可修改的 **Draft Reply**。

讀寫 Google Chat 一律使用**你本人的 OAuth 憑證**，不註冊 Chat App、不用機器人身分發話
（[ADR-0001](docs/adr/0001-user-oauth-not-bot.md)）。任何送出動作都需要二次確認。

- 需求規格：[`SPECIFICATION.md`](SPECIFICATION.md)｜領域語彙：[`CONTEXT.md`](CONTEXT.md)
- API 契約：[`docs/api-contract.md`](docs/api-contract.md)｜架構決策：[`docs/adr/`](docs/adr/)
- MCP 團隊上手手冊：[`SETUP_GUIDE.md`](SETUP_GUIDE.md)｜系統手冊：[`docs/index.html`](docs/index.html)

## 兩個入口

| 入口 | 給誰 | 怎麼跑 |
| :--- | :--- | :--- |
| **Web 儀表板** | 內部使用 | `./scripts/start-web.sh` → http://localhost:8000 |
| **MCP server** | 發給團隊每個人安裝 | `./scripts/install-claude.sh`，或照 [`SETUP_GUIDE.md`](SETUP_GUIDE.md) |

兩者是同一套後端能力的兩個入口，不是替代關係。

## 前置作業

```bash
# 1. AI 供應商（摘要與 Draft Reply 需要；只讀 Space 與訊息不需要）
#    最省事：裝好並登入 Claude Code，什麼都不用設（預設就會用它）
#    另一條路：
export GOOGLE_API_KEY='...'       # 走 Gemini（免費層每天 20 次）

# 2. Python 環境（3.12）
uv venv --python 3.12 .venv
uv pip install -r requirements.txt

# 3. Google OAuth：把 client_secret.json 放進 config/，然後跑授權精靈
.venv/bin/python mcp_app/setup_wizard.py
```

## AI 供應商（可切換）

摘要與 Draft Reply 的 AI 供應商是可選的，預設 **Claude**：

| 值 | 需要什麼 | 適用 |
| :--- | :--- | :--- |
| `claude`（預設） | 同 `claude_cli` | **別名**，目前解析為本機 Claude Code CLI |
| `auto` | — | **別名**，依序試 `claude_cli` → `gemini`，挑第一個可用的 |
| `claude_cli` | 本機裝好並登入 Claude Code | 吃現有訂閱、零額外設定 |
| `gemini` | `GOOGLE_API_KEY` | 既有選項；免費層每天僅 20 次請求 |

> Anthropic API 供應商（`claude_api`）已於 2026-09-05 移除。它從未對真實 API 跑過，
> 留著等於在契約與前端選單裡放一個沒人驗過的選項。要接回來的話，
> 實作在 git 歷史裡（`core/providers/claude_api.py`）。

```bash
# 看目前有哪些可用（三個入口都查得到）
curl -s localhost:8000/api/v1/providers          # 儀表板
./scripts/run_summary.sh --list-providers        # CLI
# MCP 則是 list_ai_providers 工具

# 指定供應商
export CHATPULSE_AI_PROVIDER=claude_cli          # 伺服器預設
./scripts/run_summary.sh "0.暫存" 50 --provider gemini   # 單次覆寫
```

儀表板的下拉選單、CLI 的 `--provider`、MCP 的 `provider` 參數三者接受同一組值。
Viewer 也可以把選擇存成偏好（`default_provider`）。

> ⚠️ **Gemini 免費層 `gemini-3.6-flash` 每天只有 20 次請求**（2026-09-05 實測）。
> 這是改成可切換供應商的直接原因——見 `SPECIFICATION.md` 的風險 R-4。
>
> **Claude Code CLI 的成本注意**：它預設會載入 Claude Code 自己的 system prompt、
> CLAUDE.md 與全部工具定義（實測一個 2-token 的 prompt 也會寫入 19,085 token 的快取）。
> 本專案的實作已固定停用工具與 MCP、並用自己的 system prompt 取代，實測降到接近 0。

## Web 儀表板

```bash
./scripts/start-web.sh
```

首次執行會順便建置前端（React 19 + Vite）。啟動後在畫面上選「匯入既有憑證」或
「使用 Google 登入」。功能：

- **Space 清單**：436 個空間的名稱模糊搜尋、強制刷新（5 分鐘快取）
- **單群摘要**：SSE 逐字串流，三種風格（通用／技術細節／只要待辦）輸出的章節結構不同
- **Action Items**：從摘要抓出待辦轉成可勾選清單，可複製為 Markdown
- **推播回 Chat**：以你本人身分送出，需二次確認
- **Mention 收件匣**：跨所有 Space 找出誰 @ 了你，待處理／已處理兩種狀態
- **Draft Reply**：可勾選 1~N 個 **Reference Space** 補充脈絡（預設不勾），
  串流輸出「脈絡分析」與「建議回話」，改完後以你本人身分回到原討論串

## CLI 單群摘要

```bash
# 摘要「0.暫存」最近 50 則（預設值），只在終端輸出
./scripts/run_summary.sh "0.暫存"

# 指定則數與風格
./scripts/run_summary.sh "1.BU2-PG" 100 --style technical

# 要推播回群組必須明確加 --post，且會再要求一次確認
./scripts/run_summary.sh "0.暫存" 50 --post
```

**不加 `--post` 就不會推播。** 則數可用範圍 1~1000，預設 50——這組上下限四個入口
（REST／SSE／MCP／CLI）共用同一份常數。

## 測試

E2E 測試會對**真實的 Google Chat 與 Gemini API** 發請求，不使用 mock。
發訊息的目標被限制在單一測試用 Space，`tests/e2e/e2e_lib.py` 的 `guard_space()` 會擋掉其他目標。

```bash
# 先啟動服務，再跑
.venv/bin/python tests/e2e/run_all.py

# 前端單元測試
npm --prefix dashboard/frontend run test
```

七套測試分別涵蓋：**落地證據重查**（不呼叫 AI）、**AI 供應商切換**、Phase 1 功能、
Phase 2 Mention 與 Draft Reply、缺陷 D-3 的截斷正負對照、6.1 判定條件（含真實
ADD annotation 樣本）、靜態托管與路徑穿越防護。

```bash
# 只想確認「已產生的東西是對的」而不想消耗 Gemini 配額，跑這支就好
.venv/bin/python tests/e2e/check_stored_evidence.py
```

它直接從 SQLite 重查宣稱：摘要的章節結構、三種風格的差異、Reference Space 的
正負對照配對、token 用量的組成、名錄與真實 Mention。Gemini 免費層每天只有
20 次請求，這支腳本讓大部分驗證不受那個限制。

## 專案結構

```
core/        共用封裝，不含任何入口（Google Chat／Gemini／SQLite／加密／名錄／採集器）
mcp_app/     MCP 入口——發給團隊安裝（四個 MCP 工具、CLI 摘要、OAuth 授權精靈）
dashboard/   儀表板——內部使用（FastAPI 後端 ＋ React 前端）
config/      憑證（不進版控）
data/        SQLite 與加密金鑰（不進版控）
docs/adr/    架構決策
tests/e2e/   端對端測試
```

依賴方向單向：`mcp_app → core ← dashboard`，兩個入口互不 import
（[ADR-0005](docs/adr/0005-single-repo-three-layers.md)）。

## 資料處理邊界

**沒有 Space 排除清單**：所有你有權讀取的 Space，其對話內容都可以被送去 Gemini
產生摘要與草稿，**包含公部門與金融客戶專案群組**。對話全文不落地，但摘要與草稿會以
明文存在本機 SQLite 中 90 天。這是已經做出的決定，理由與代價寫在
`SPECIFICATION.md` 第十節，團隊共用前請先讀那一節。
