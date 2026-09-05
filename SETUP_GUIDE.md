# ChatPulse 團隊上手手冊（MCP Server ＋ Web 儀表板）

歡迎使用 **ChatPulse**！本工具有**兩個入口**，用的是同一套後端能力：

| 入口 | 給誰 | 怎麼跑 |
| :--- | :--- | :--- |
| **MCP Server** | 想在 Claude Code / Claude Desktop 對話中直接用的人 | `./scripts/install-claude.sh` |
| **Web 儀表板** | 想要完整功能（Mention 收件匣、Draft Reply）的人 | `./scripts/start-web.sh` |

能做的事：
- 🔍 搜尋與列出您加入的所有 Google Chat 空間。
- 📥 抓取特定群組的最新歷史對話（1~1000 則，預設 50）。
- 🧠 產出結構化摘要，三種風格（通用／技術細節／只要待辦）。
- 📬 **Mention 收件匣**：跨所有群組找出誰 @ 了你（僅儀表板）。
- ✍️ **Draft Reply**：針對一則 @ 產生可直接送出的回話（僅儀表板）。
- 💬 以**你本人的身分**發送訊息或回推摘要（不是機器人，任何送出都要二次確認）。

---

## 前置需求（先確認這兩件事）

**1. 裝好並登入 Claude Code。** 摘要與 Draft Reply 預設走 `claude_cli` 供應商，
它直接用你**本機 Claude Code 的登入狀態**（吃你現有的訂閱），不需要任何 API key。
在終端執行 `claude` 能正常進入對話，就代表沒問題。

> 沒有 Claude Code 也能用讀取類功能（列群組、抓訊息），但**一按摘要就會失敗**，
> 錯誤訊息是「找不到 claude 指令」。另一條路是設 `GOOGLE_API_KEY` 走 Gemini，
> 但免費層**每天只有 20 次請求**，多人共用會當天用完。

**2. 裝好 Node.js（只有要用 Web 儀表板才需要）。** 前端建置產物不進版控，
第一次跑 `start-web.sh` 會自動 `npm install` 並建置，需要幾分鐘。
只用 MCP 的話不需要 Node。

Python 與 `uv` **不用自己準備**——安裝腳本偵測不到 `uv` 會自動幫你裝。

---

## 快速上手（2 步驟）

### 步驟 1：取得專案與 Client Secret 憑證
1. 將本專案 git clone 至您的電腦（**專案庫是 private，請向維護者索取存取權**）：
   ```bash
   git clone https://github.com/mark22013333/ChatG-Bot.git google-chat-bot
   cd google-chat-bot
   ```
2. 向專案維護者（如 Mark）索取共用的 `client_secret.json`。
   **`config/` 目錄不在版控裡，clone 之後不會存在，要自己建**：
   ```bash
   mkdir -p config
   # 然後把拿到的 client_secret.json 放進去
   ```
   最後應該長這樣：
   ```text
   google-chat-bot/config/client_secret.json
   ```
   > 💡 **Client Secret 與 Token 的差別**：
   > - `client_secret.json`（客戶端金鑰）：**團隊共用**，代表這個 App 是公司合格的 Google 應用程式。
   > - `google_chat_token.json`（個人 Token）：**個人專屬**，絕不能直接複製別人的，必須透過下一步登入產生您自己的 Token（決定您能看見哪些群組）。

### 步驟 2：執行一鍵新手導引精靈
在終端機中執行：
```bash
./scripts/setup.sh
```
這個精靈會：
1. 自動檢查 Python 與 `uv` 環境。
2. 自動開啟瀏覽器跳出 Google 登入視窗，請**選擇您的公司 Google Workspace 帳號並同意授權**。
3. 授權成功後，精靈會自動在終端機輸出符合您電腦路徑的 **MCP JSON 設定代碼**。

---

## 註冊到 Claude Desktop / Claude Code

將精靈產生的 JSON 代碼加入您的 Claude 設定檔中：

### 1. Claude Desktop (桌面版)
開啟或編輯：
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

加入以下區塊：
```json
{
  "mcpServers": {
    "google-chat": {
      "command": "uv",
      "args": [
        "run",
        "--with", "mcp",
        "--with", "google-api-python-client",
        "--with", "google-auth-oauthlib",
        "--with", "google-auth-httplib2",
        "--with", "requests",
        "--with", "cryptography",
        "python3",
        "/絕對路徑/google-chat-bot/mcp_app/mcp_server.py"
      ]
    }
  }
}
```

### 2. Claude Code (CLI 命令列)
開啟或編輯 `~/.claude.json`，在 `"mcpServers"` 節點內加入上述相同的 `"google-chat"` 設定。

重啟 Claude 即可開始使用！

> **只有走 Gemini 供應商的 Claude Desktop 使用者需要這一步**：Desktop 是 GUI 程式，
> **不會讀取 `~/.zshrc`**，所以 `GOOGLE_API_KEY` 要寫在上面 JSON 的 `env` 區塊裡：
> ```json
> "env": { "GOOGLE_API_KEY": "你的金鑰" }
> ```
> 預設的 `claude_cli` 供應商**不需要**這一步。從終端啟動的 Claude Code 會繼承
> shell 環境，也不受此限。

---

## 常用對話範例 (自然語言指令)

在 Claude 中您可以直接這樣問：

- **列出群組**：
  > 「幫我看一下我的 Google Chat 有哪些群組，找找看『北市府』相關的」
- **抓取與摘要對話**：
  > 「讀取『1.BU2-PG』最近 100 筆對話，並幫我整理重點與 Action Items」
- **指定摘要風格**（三種：`general` 通用、`technical` 技術細節、`action_only` 只要待辦）：
  > 「用技術細節的風格摘要『[技術發問區] SmartRobot / SmartBC』最近 100 則」
- **摘要並回推**：
  > 「分析『0.暫存』最近 50 則訊息，並把摘要結果發送回群組」
- **發送文字**：
  > 「幫我在『0.暫存』發送：今天下午 3:00 開會」

---

## 常見問題 (FAQ)

### Q1：如果我自己想在 GCP 建立專屬的 Client Secret 怎麼做？
下面每一步都給直接網址（把 `PROJECT_ID` 換成你的專案 ID）。**刻意不寫
「點某個選單再點某個分頁」的路徑**——Google Console 改版頻繁，那種描述過期時
不會有任何跡象，網址至少會直接 404 告訴你。

1. 建立專案：<https://console.cloud.google.com/projectcreate>
2. 啟用 Google Chat API：
   <https://console.cloud.google.com/apis/library/chat.googleapis.com?project=PROJECT_ID>
3. 建立 OAuth 用戶端（類型選 **電腦版應用程式 / Desktop app**）：
   <https://console.cloud.google.com/auth/clients?project=PROJECT_ID>
4. 設定 Chat app 的名稱與說明（這個名字會出現在你送出的每一則訊息旁邊，見 Q6）：
   <https://console.cloud.google.com/apis/api/chat.googleapis.com/hangouts-chat?project=PROJECT_ID>
4. 下載 JSON 並重新命名為 `client_secret.json` 放入 `config/` 即可。

### Q2：推播回群組失敗，是不是要先把 Bot 加進群組？
**不是，這個系統沒有 Bot。** 讀訊息和發訊息用的都是**你本人的 Google 身分**，
所以只要你在那個群組裡就能發言，不需要邀請任何應用程式進去。
（舊版本手冊在這裡寫過「請加入該機器人」，那是錯的，已更正。）

真正的原因通常是：
- `403 SPACE_FORBIDDEN`：你不是該群組成員，或授權時沒給 `chat.messages.create` 權限 → 重跑授權精靈
- `404 SPACE_NOT_FOUND`：群組 ID 打錯，或你已退出該群組
- `401 NOT_AUTHENTICATED`：儀表板登入過期 → 重新登入

### Q3：摘要一直失敗，說找不到 claude 指令？
你的機器沒有裝 Claude Code，或沒有登入。見本文最上方的「前置需求」。
想確認目前有哪些 AI 供應商可用，執行：
```bash
./scripts/run_summary.sh --list-providers
```

### Q4：怎麼用 Web 儀表板？
```bash
./scripts/start-web.sh
```
它會自己建好 Python 環境、建置前端、然後開瀏覽器到 http://localhost:8000。
第一次會比較久（要 `npm install`）。

登入方式：如果你已經跑過 `./scripts/setup.sh`，儀表板會直接認得你既有的授權，
**不必再登入一次**（畫面上會有「匯入既有憑證」的選項）。沒跑過精靈的話，
就在儀表板上直接按 Google 登入。

### Q5：怎麼確認我裝好了？
```bash
./scripts/doctor.sh
```
它會逐項檢查 Python 環境、憑證、Token 權限、AI 供應商、MCP 註冊、前端建置，
**每一項沒過都會直接告訴你下一步該做什麼**。裝完先跑這個，比一項項猜快。

### Q6：為什麼我送出的訊息旁邊多了一個灰色標籤？

那是 Google Chat 的**歸屬標示**。只要訊息是透過 API 送出的，Chat 就會在你的名字旁邊
顯示這個工具的名稱，長得像這樣：

```
你  [ChatPulse]  中午12:36
```

官方文件（[create-messages](https://developers.google.com/workspace/chat/create-messages)）
逐字寫著「Chat also attributes the Chat app to the message by displaying its name」，
所以這是**設計行為，不是設定錯誤**。

- **發訊息的人還是你本人**。API 回讀確認 `sender.type` 是 `HUMAN`、user id 是你自己，
  不是機器人代你發言。標籤只是說明「這則是透過工具送的」。
- **關不掉**。`spaces.messages.create` 沒有任何 attribution 相關參數，也沒有對應的
  scope 或 Console 開關。（精確地說：官方**未提供**關閉手段，而不是官方明令禁止。）
- **標籤文字可以改**，改的是 Chat API 的 App name（上限 25 字元）：
  <https://console.cloud.google.com/apis/api/chat.googleapis.com/hangouts-chat?project=PROJECT_ID>
  改名**不會**讓你需要重新授權。這件事只有專案維護者需要做，一次改完所有人都適用。
