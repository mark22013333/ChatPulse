# Google Chat MCP Server 團隊協作與快速上手手冊

歡迎使用 **Google Chat Assistant MCP Server**！  
本工具讓您能夠直接在 **Claude Desktop** 或 **Claude Code** 中，透過自然語言：
- 🔍 搜尋與列出您加入的所有 Google Chat 空間。
- 📥 快速抓取特定群組的最新歷史對話（支援 30/50/100 筆）。
- 🧠 調用 **Gemini 3.6 Flash** 深度整理對話脈絡、決策重點與待辦事項 (Action Items)。
- 💬 直接發送訊息或一鍵回推摘要至 Google Chat 群組。

---

## 快速上手（只需 2 步驟）

如果您是第一次使用的同事，請依循以下極簡流程：

### 步驟 1：取得專案與 Client Secret 憑證
1. 將本專案資料夾複製（或 git clone）至您的電腦：
   ```bash
   git clone <專案庫網址> google-chat-bot
   cd google-chat-bot
   ```
2. 向專案維護者（如 Mark）索取共用的 `client_secret.json`，並放置於：
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

> **Claude Desktop 使用者必讀**：Desktop 是 GUI 程式，**不會讀取 `~/.zshrc`**，
> 所以 `GOOGLE_API_KEY` 必須寫在上面 JSON 的 `env` 區塊裡顯式提供，否則
> MCP server 啟動時就會因為找不到 Gemini API key 而失敗。
> 從終端啟動的 Claude Code 會繼承 shell 環境，不受此限。

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
1. 前往 [Google Cloud Console](https://console.cloud.google.com/) 建立專案。
2. 在「API 和服務」>「資料庫」中啟用 **Google Chat API**。
3. 在「憑證」>「建立憑證」選擇 **OAuth 用戶端 ID**，應用程式類型選擇 **電腦版應用程式 (Desktop App)**。
4. 下載 JSON 並重新命名為 `client_secret.json` 放入 `config/` 即可。

### Q2：為什麼摘要可以讀取，但推播回群組回傳 404？
- 讀取訊息是用您個人的身分（只要群組內有您就能讀）。
- 推播發言需要該 Google Chat 群組**已經邀請了該專案的 Bot**。請在該群組點「+ 新增應用程式」加入該機器人即可。
