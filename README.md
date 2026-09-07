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
| **Web 儀表板** | 要完整功能（Mention 收件匣、Draft Reply）的人 | `./chatpulse.sh web` → http://localhost:8000 |
| **MCP server** | 想在 Claude Code / Desktop 對話中直接用的人 | `./scripts/install-claude.sh`，或照 [`SETUP_GUIDE.md`](SETUP_GUIDE.md) |

兩者是同一套後端能力的兩個入口，不是替代關係。

**發給同事時只要告訴他們一行**（互動式引導，每步都有說明，可重複執行）：

| 系統 | 指令 |
| :--- | :--- |
| Windows | 雙擊 `chatpulse.bat` |
| macOS / Linux | `./chatpulse.sh` |

子命令：`web`（啟動儀表板，日常最常用）／`check`（檢查狀態）／`auth`（重新授權）／
`mcp`（重新註冊 MCP 入口）。裝好之後直接下子命令即可，不必再跑完整引導——
引導本身也會跳過已完成的步驟。

引導流程的邏輯在 `scripts/onboard.py`，**兩個平台共用同一份**——
`.sh` 與 `.bat` 只負責找到 Python。各寫一份腳本必然漂移，
而 Windows 那份的坑，用 macOS 的維護者永遠踩不到。

維護者自己在 macOS 上也可以用既有的：

```bash
./scripts/doctor.sh
```

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
./chatpulse.sh web          # Windows：chatpulse.bat web
```

畫面（React 19 + Vite 的建置產物）已隨專案進版控，clone 下來就有，**不需要 Node.js**。
只有改前端原始碼時才需要——啟動時會比對來源雜湊，發現產物過期且裝了 `npm` 就自動重建。
啟動後在畫面上選「匯入既有憑證」或「使用 Google 登入」。功能：

- **Space 清單**：436 個空間的名稱模糊搜尋、強制刷新（5 分鐘快取）
- **單群摘要**：SSE 逐字串流，三種風格（通用／技術細節／只要待辦）輸出的章節結構不同
- **Action Items**：從摘要抓出待辦轉成可勾選清單，可複製為 Markdown
- **推播回 Chat**：以你本人身分送出，需二次確認
- **Mention 收件匣**：跨所有 Space 找出誰 @ 了你，待處理／已處理兩種狀態
- **Draft Reply**：可勾選 1~N 個 **Reference Space** 補充脈絡（預設不勾），
  串流輸出「脈絡分析」與「建議回話」，改完後以你本人身分回到原討論串
- **參考專案**：登錄本機 git repo 並指定哪個分支是正式、哪個是 UAT，草稿就能
  引用**實際程式碼**回答「這段邏輯為什麼這樣寫」，並明確標示是哪個環境的哪個
  commit（[ADR-0006](docs/adr/0006-manual-reference-projects-over-code-rag.md)）
- **回覆設定**：產草稿前選語氣、套個人風格、開潤稿，只影響「建議回話」那一段
  （見下）

### 回覆設定（Draft Reply）

同一份事實用錯語氣送出去一樣是失敗的。產草稿時可以逐次選這四項，**全部選填，
一個都不選就跟這個功能不存在時完全一樣**。四項都只影響〈建議回話〉，
〈脈絡分析〉不受影響（[ADR-0007](docs/adr/0007-sanitized-text-over-skill-runtime.md)）。

| 設定 | 怎麼用 |
| :--- | :--- |
| **Reply Tone** | 8 種語氣的下拉選單：自然直接／專業正式／簡潔明確／親切友善／工程師協作／委婉柔和／堅定明確／自訂。**與摘要的三種風格是不同的東西**——那個決定章節結構，這個決定回話語氣 |
| **Persona** | 一組個人寫作風格（思考方式、表達習慣、要避的寫法）。**不是角色扮演**——回話永遠以你本人的身分送出，不會自稱是別人 |
| **Reply Prompt Preset** | 把常用的自訂要求存起來重複套用。當次直接打在輸入框的字優先於已存的 Preset |
| **潤稿**（Sepia） | 草稿產完後對〈建議回話〉再修一次，讓它不讀起來像機器寫的。**預設關閉** |

四項都可以在設定頁存成預設值，之後每次產草稿自動帶入；單次選擇一律覆寫預設值。

**Persona 怎麼匯入**：在設定頁貼一個公開的 GitHub repo（`owner/repo`）先列出裡面
有哪些可匯入，選一個匯入；或直接貼一份風格描述自己建一個。匯入時會**釘住當下的
commit SHA**——今天產生的回話不該因為遠端明天改了檔案就變一個樣子；要更新按
「更新 Persona」，系統會告訴你內容有沒有真的變。

匯入的原文**不會直接送給模型**。這類檔案常內含「直接以某人的身分回應」「遇到沒寫到的
事可以用框架推斷」這種指令，前者會讓回話冒名、後者會撞掉 ChatPulse「不臆測沒出現過
的資訊」的規則。所以原文一律先經淨化，只有結構化的風格資訊會進 prompt；淨化後沒剩
東西的來源會被拒絕（那是正常結果，不是壞掉）。

**Sepia 是什麼**：一套「去 AI 味」的寫作規則（[MIT](https://github.com/Nanako0129/sepia)）。
ChatPulse 把它的最小規則子集 **vendored 進本 repo**（`core/polishers/sepia_rules/`，
版本與來源 commit 記在 `VERSION.json`），**不透過 Claude Code 的 skill 載入機制**——
那要拆掉本專案刻意加的成本控制旗標（上面那條 19,085 token 的注意事項），還會讓草稿
偷偷受你本機 CLAUDE.md 影響。潤稿用的是你這次選的同一個供應商，token 用量與草稿本身
分開記帳。它只做最小幅度修訂，改壞事實會被擋下並退回未潤稿的版本。

規格權威在 [`SPECIFICATION.md`](SPECIFICATION.md) 7.4，端點契約在
[`docs/api-contract.md`](docs/api-contract.md)。

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

**參考專案會把原始碼片段送給 AI**：只限你自己登錄的 repo（不是自動掃描），
片段本身不落地，但草稿裡的程式碼會跟著草稿存 90 天。`.env` 之類的路徑整份跳過、
片段內的金鑰會遮蔽，但那是 best-effort。要全面關閉：`CHATPULSE_CODE=0`。
細節見 `SPECIFICATION.md` 10.4。
