# ChatPulse 團隊上手手冊（MCP Server ＋ Web 儀表板）

歡迎使用 **ChatPulse**！本工具有**兩個入口**，用的是同一套後端能力：

| 入口 | 給誰 | 怎麼跑 |
| :--- | :--- | :--- |
| **MCP Server** | 想在 Claude Code / Claude Desktop 對話中直接用的人 | `./scripts/install-claude.sh` |
| **Web 儀表板** | 想要完整功能（Mention 收件匣、Draft Reply）的人 | `./chatpulse.sh web` |

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

**2. Node.js 不用裝。** 儀表板的畫面（前端建置產物）已經隨專案一起進版控，
clone 下來就有，開了就能用。只有**要改前端原始碼**的人才需要 Node.js——
啟動時會自動偵測原始碼比產物新，有 `npm` 就順手重建。

Python 與 `uv` **不用自己準備**——安裝腳本偵測不到 `uv` 會自動幫你裝。

---

## 最快的路：一個指令走完全部

不想讀完整份手冊的話，只要記這一行。它會一步一步問你、每一步都解釋在做什麼，
**隨時可以重複執行**（已經做好的步驟會自動跳過）：

| 系統 | 指令 |
| :--- | :--- |
| **Windows** | 在專案資料夾裡**雙擊 `chatpulse.bat`**，或在命令提示字元執行 `chatpulse.bat` |
| **macOS / Linux** | `./chatpulse.sh` |

它會依序處理：Python 環境 → 憑證檢查 → Google 授權 → 選擇入口並安裝。
中途卡住的話，每一項都會直接告訴你下一步該做什麼。

其他常用子命令（Windows 把 `./chatpulse.sh` 換成 `chatpulse.bat`）：

```bash
./chatpulse.sh check    # 只檢查安裝狀態，不動任何東西
./chatpulse.sh auth     # 只重新做 Google 授權
./chatpulse.sh web      # 只啟動 Web 儀表板
```

> **Windows 使用者請先看這兩點**
> 1. **裝 Python 時務必勾選「Add python.exe to PATH」**（在安裝畫面最下方）。
>    沒勾的話 `chatpulse.bat` 會找不到 Python，而那是最常見的卡關原因。
> 2. **不要把專案放在 OneDrive 同步資料夾裡。** 這個工具用 SQLite 儲存資料，
>    它的檔案鎖在同步資料夾或網路磁碟上會失敗。放本機路徑（例如 `C:\ChatPulse`）。

下面是逐步說明，想知道每一步在做什麼、或自動安裝失敗時可以照著手動做。

---

## 快速上手（2 步驟）

### 步驟 1：取得專案與 Client Secret 憑證
1. 將本專案 git clone 至您的電腦（**專案庫是 private，請向維護者索取存取權**）：
   ```bash
   git clone https://github.com/mark22013333/ChatPulse.git google-chat-bot
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
./chatpulse.sh web          # Windows：chatpulse.bat web
```
它會自己建好 Python 環境、確認畫面備妥，然後開瀏覽器到 http://localhost:8000。
畫面已經隨專案附上，所以**不需要 Node.js，也不用等建置**。

（`./scripts/start-web.sh` 也還能用，跑的是同一段程式碼。）

登入方式：儀表板第一次開起來會停在登入畫面，有兩個按鈕。

- 已經授權過的人（跑過引導的步驟 3，或 `./scripts/setup.sh`）按 **「匯入既有憑證」**。
  這只是把本機那份憑證匯入儀表板，**不會再跑一次 Google 授權、不會開瀏覽器要你選帳號**。
- 沒授權過的人按「使用 Google 登入」，走完整的授權流程。

要注意兩件事，這是實測後的行為，不是設計缺陷：

1. **這個按鈕要自己按一下**，不會自動匯入。原因是儀表板與 MCP 的憑證是分開存的
   （MCP 讀 `config/google_chat_token.json`，儀表板讀資料庫裡的加密副本），
   匯入是把前者複製給後者。
2. **儀表板的登入狀態 14 天後過期**，過期後要再按一次同一個按鈕。
   這只是儀表板自己的 session，**你的 Google 授權不受影響**，
   所以不會再跳出 Google 的同意畫面。

### Q5：怎麼確認我裝好了？
```bash
./chatpulse.sh check        # Windows：chatpulse.bat check
```
它會逐項檢查 Python 環境、憑證、授權權限、AI 供應商、儀表板畫面，
**每一項沒過都會直接告訴你下一步該做什麼**。裝完先跑這個，比一項項猜快。

（`./scripts/doctor.sh` 也還在，多檢查一項 MCP 註冊狀態，但只有 macOS／Linux 能跑。）

### Q7：Windows 上有什麼不一樣的地方？

功能完全一樣，但有三點差異要知道：

1. **入口是 `chatpulse.bat`**（可雙擊）。`scripts/` 底下那些 `.sh` 檔在 Windows 上跑不了，
   對應功能請用 `chatpulse.bat check` / `auth` / `web`——這些子指令跑的程式碼與
   macOS 完全相同（都是 `scripts/onboard.py` 與 `scripts/webapp.py`），不是另寫一份。
2. **檔案權限保護不生效**。`config\` 與 `data\` 裡有你的 Google 授權憑證，
   在 macOS/Linux 上會被設成「只有你讀得到」，但 Windows 沒有對應的機制。
   **請不要在共用電腦上使用**。這是作業系統差異，不是設定錯誤，
   `chatpulse.bat check` 也會提醒你這件事。
3. **不要放在 OneDrive 同步資料夾**（見本文最上方的說明）。
4. **`chatpulse.bat` 本身的訊息是英文的**。這個檔案必須全檔 ASCII——cmd.exe 用
   位元組偏移記住批次檔讀到哪，而 `chcp 65001` 會讓後面的位元組改用 UTF-8 解讀，
   中文字（3 位元組）一出現就會讓讀取位置落在字元中間，把註解的後半段當成指令執行
   （症狀是一堆 `'xxx' is not recognized as an internal or external command`）。
   Python 啟動之後的所有訊息都是中文，不受影響。
   你只會在**找不到 Python** 時看到那段英文，它說的是：
   - 到 <https://www.python.org/downloads/> 下載安裝（需要 3.10 以上，建議 3.12）
   - **安裝時務必勾選「Add python.exe to PATH」**（在安裝畫面最下方），
     否則裝完 `chatpulse.bat` 還是找不到它
   - 裝好之後重新執行 `chatpulse.bat`
5. **專案路徑不要含 `&`、`%`、`^`、`|`、`<`、`>`**（例如 `C:\Users\你\R&D\`）。
   這些字元對 Windows 命令列有特殊意義，而 `claude` 指令在 Windows 上是批次檔，
   路徑會被多解析一次，導致 MCP 註冊失敗。引導會事先偵測並提醒你。

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
  > ⚠️ 正因為「所有人都適用」，**這個名字不可以取成任何人的名字**——
  > 取成維護者的名字，同事的訊息就會顯示成 `王小明 [某某某] 12:36`，
  > 看起來像是別人代發的。要取**工具名**。
