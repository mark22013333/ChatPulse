# CLAUDE.md — ChatPulse

給工程團隊使用的 Google Chat 輔助工具。領域詞彙（Viewer／Space／Mention／Draft Reply…）
與它們的 _Avoid_ 清單在 `CONTEXT.md`，**下筆前先讀那一份**——這個專案對用詞很嚴格，
把 Viewer 寫成「使用者」、把 Space 寫成「群組」都算錯。

## 語言

所有輸出一律**繁體中文**：對話、程式註解、docstring、commit message、錯誤訊息。
技術名詞與程式碼識別字保持原文。

## 技術棧

| 層 | 內容 |
|----|------|
| Python | 3.12（`.venv`，用 `uv` 建立與安裝） |
| 後端 | FastAPI 0.141 + uvicorn[standard]（SSE 與 `--reload` 需要 standard） |
| 儲存 | SQLite，DDL 全部在 `core/db.py` 的字串常數裡 |
| 外部呼叫 | `requests`（**沒有** AI SDK，Gemini 直接打 REST，見 `requirements.txt` 的說明） |
| 前端 | React 19 + Vite + TypeScript + Tailwind + zustand + Base UI／shadcn |
| 測試 | 後端 `unittest`、前端 `vitest` |

## 分層（ADR-0005）

```
mcp_app → core ← dashboard
```

- `core/`：共用封裝與純邏輯，**不含入口**。兩個入口互不 import。
- `mcp_app/`：MCP server、CLI、OAuth 授權精靈。發給團隊每個人安裝。
- `dashboard/`：FastAPI 後端（`dashboard/api/server.py`）＋ 前端。

`core/` 內部還有一條更細的界線，**動 worklog 相關程式碼時特別要守**：
`worklog_allocation.py`、`worklog_clustering.py`、`worklog_evidence.py` 是
**純邏輯，不碰 DB、不碰網路**；碰 DB 的是 `repository.py`，碰外部 API 的是
`chat_client.py`／`gemini_client.py`／`zplanner_client.py`。讓純邏輯模組去
import repository 或 client，界線就守不住了。

`core/` 要保持薄。dashboard 特有的需求寫進 `core` 會讓發給同事的 MCP 背上儀表板的複雜度。

## 指令

```bash
.venv/bin/python -m unittest discover -s tests/unit   # 後端單元測試（零 API、零配額）
cd dashboard/frontend && npm test                      # 前端 vitest
cd dashboard/frontend && npm run typecheck             # tsc --noEmit
cd dashboard/frontend && npm run build                 # 產物進 dist/（有進版控）
```

改完後端一律跑一次 `unittest discover`；改完前端跑 `typecheck` ＋ `test`。

## 註解與 docstring 的風格（這個 repo 最鮮明的特徵）

讀一次 `core/zplanner_client.py` 或 `core/db.py` 就知道標準在哪。要點：

1. **寫「為什麼」，不寫「做什麼」。** 程式碼自己會說做什麼；註解要說的是
   「不這樣寫會怎麼壞」。
2. **先講會怎麼壞。** 反直覺的地方，開頭就寫出失敗的樣子（例：「這支 API 的
   HTTP 狀態碼恆為 200，任何靠 `raise_for_status()` 判斷成敗的程式碼，會把權限
   不足當成功處理」）。
3. **斷言要帶證據與日期**，格式如「2026-09-14 實測：…」。沒有實測就寫「未驗證推論」。
4. **記下被否決的做法與否決理由**，不要只留結論。
5. `🔴` 標「違反了會出事」，`⚠️` 標「反直覺、容易踩」。

## 硬規則

- **認不出來就拋，絕不猜。** 外部回應的形狀不符預期時一律拋例外，不要回空
  list／None 讓呼叫端以為「查詢成功但沒有資料」——那是這個 codebase 認定最貴的
  失敗模式。同理，靜默少給資料（只讀第一頁、截斷後回傳）一律拋。
- **錯誤型別要分得夠細，判準是「下一步動作不同」。** 見 `core/errors.py`：每個
  類別的 docstring 都寫明「為什麼不與某某合併」。新增錯誤前先讀那份。
- **查詢回 0 筆不等於資料不存在。** 下這種結論前要有正對照（用已知必然有資料的
  條件打同一支端點，證明管道是通的）。
- **秘密與內部網域不進版控**（這個 repo 是公開的）。憑證、token、公司內部網址
  一律走環境變數；測試 fixture 用假值。`tests/unit/test_zplanner_client.py` 有一條
  測試會掃描檔案擋這件事。
- **不要在測試裡斷言錯誤訊息的措辭**，改斷言行為或關鍵資料。文案會隨著理解修正
  而改寫，把 prose 釘進測試會讓「更正一個錯誤的說明」看起來像弄壞了功能。

## 設計決策

`docs/adr/` 下有 8 份 ADR，動到對應範圍前先讀：

| ADR | 主題 |
|-----|------|
| 0001 | 以 Viewer 的 OAuth 身分行動，不用 bot 身分 |
| 0002 | Summary 屬於產生它的 Viewer，其他人看不到 |
| 0005 | 單一 repo 三層 |
| 0008 | Draft Worklog（工時草稿）；**硬規則：產生時歸屬一律留空，選擇權在 Viewer** |

API 契約在 `docs/api-contract.md`，新增或修改端點要同步更新那一份。
