# 單一 repo 內分三層，不拆成多個 repo

程式碼集中在單一 repo（`github.com/mark22013333/ChatG-Bot`，private），內部分為三層：`core/`（Google Chat 與 Gemini 的共用封裝，不含入口）、`mcp_app/`（MCP server、CLI 摘要、OAuth 授權精靈）、`dashboard/`（FastAPI 後端與前端）。依賴方向單向：`mcp_app → core ← dashboard`，兩個入口互不 import。

拆分的動機是**發布對象不同**：`mcp_app` 要發給團隊每個人安裝，`dashboard` 是內部服務。但拆成獨立 repo 的代價（共用碼要嘛複製兩份、要嘛做成需要 git remote 與版本管理的可安裝套件）在這個規模上不划算——`core` 目前只有三支檔案。單一 repo 讓兩個入口直接以套件路徑 import，共同缺陷（例如 `core/chat_client.py:28` 忽略 `nextPageToken`，導致超過 100 個 Space 讀不到）只需修一次。

## Considered Options

- **三個獨立 repo，core 做成 pip 可安裝套件**：隔離最徹底，但需要一個兩邊都連得到的 git remote、一套發布流程，且每次改 core 都要更新兩邊的依賴版本。對三支檔案而言，管理成本高於收益。
- **兩個 repo，core 複製兩份**：起步最快，但共同缺陷要修兩次。`chat_client.py` 正是最容易出錯的一層（分頁、配額、重試），複製它風險最高。

## Consequences

- 發 `mcp_app` 給同事時，他們會一併取得 `dashboard` 的程式碼。這是單一 repo 方案接受的代價；若日後 dashboard 涉及不宜外流的內容，需重新評估拆分。
- **目錄名為 `mcp_app/` 而非 `mcp/`**：`mcp` 是 MCP SDK 自身的套件名，而程式啟動時會把專案根目錄插入 `sys.path[0]`（`mcp_app/mcp_server.py:4-6`）。若目錄叫 `mcp/`，`from mcp.server.mcpserver import MCPServer` 會解析到專案自己的目錄而不是 SDK，import 直接失敗，且錯誤訊息不會指出真正原因。
- `core` 應保持薄封裝。dashboard 特有的需求（多 Viewer token 管理、Mention 搜尋、分頁採集）若寫進 `core`，會讓發給團隊的 MCP 背上儀表板的複雜度。
- 搬移使 `mcp_server.py` 的絕對路徑改變，`/Users/cheng/.claude.json` 與 Claude Desktop 設定檔兩處的 MCP 註冊必須同步更新。
- 憑證（`google_chat_token.json`、`client_secret.json`）與 `core/config.py` 的 Gemini key 一律不進版控，於建立版控前已處理完畢。
