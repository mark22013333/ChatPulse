# Repository Guidelines

## 專案結構與模組分工

ChatPulse 提供 Google Chat 的 Summary、Mention 收件匣與 Draft Reply。修改前先讀 `CONTEXT.md`、`CLAUDE.md` 與相關 `docs/adr/`。

- `core/`：共用邏輯、SQLite、OAuth 與外部 API client；資料庫存取集中在 `repository.py`，工時分配、分群與證據模組維持純邏輯。
- `mcp_app/`：MCP、CLI 與 OAuth 授權精靈。
- `dashboard/api/`：FastAPI；`dashboard/frontend/src/`：React、TypeScript、Zustand 與樣式資源。
- `tests/unit/`、`tests/e2e/`：後端單元測試與整合驗證；前端測試與原始碼並置。
- `scripts/`：跨平台啟動與環境檢查；`docs/`：API 契約及架構文件。

遵守 `mcp_app → core ← dashboard`；兩個入口不得互相 import。

## 建置、測試與本機開發

以下指令從儲存庫根目錄執行：

```bash
uv venv --python 3.12 .venv
uv pip install -r requirements.txt
./chatpulse.sh check                         # 檢查環境與憑證
./chatpulse.sh web                           # 儀表板：http://localhost:8000
npm --prefix dashboard/frontend ci           # 安裝前端鎖定依賴
npm --prefix dashboard/frontend run dev      # Vite：5173，API 代理至 8000
npm --prefix dashboard/frontend run typecheck
npm --prefix dashboard/frontend test
npm --prefix dashboard/frontend run build    # 輸出至 dist/
.venv/bin/python -m unittest discover -s tests/unit
```

Windows 使用 `chatpulse.bat web`；OAuth 設定請依 `README.md` 操作。

## 程式風格與命名

Python 使用四個空白、`snake_case` 函式與模組；TypeScript 使用兩個空白，元件採 `PascalCase`，hook 採 `useXxx`。沿用鄰近檔案風格；目前未設定統一 formatter 或 lint 指令。

註解、docstring、錯誤訊息及提交說明使用繁體中文；保留 Viewer、Space 等領域詞彙。註解解釋原因與失敗情境。外部回應格式異常時拋出明確錯誤，不得以空資料掩蓋失敗。

## 測試準則

後端使用 `unittest`，命名為 `test_*.py`；前端使用 Vitest，純邏輯為 `*.test.ts`（Node），元件為 `*.test.tsx`（jsdom、Testing Library）。後端修改須跑完整單元測試；前端修改須跑 `typecheck` 與 `test`。目前未設定覆蓋率門檻；新增測試應驗證可觀察行為，避免綁定錯誤文案。

E2E 須先啟動服務，再執行 `.venv/bin/python tests/e2e/run_all.py`；它會呼叫真實 Google Chat／AI API，發送目標必須通過 `guard_space()`。

## Commit 與 Pull Request

沿用 Git 歷史的 `type(scope): 繁體中文說明`，例如 `fix(mentions): 修正清單與計數不一致`；常見類型為 `feat`、`fix`、`refactor`、`chore`。

PR 請說明問題、變更行為、測試結果與相關 issue；介面變更附截圖。修改端點時同步更新 `docs/api-contract.md`。前端功能變更須重建並提交已納入版控的 `dashboard/frontend/dist/`。

## 安全與設定

憑證放在 `config/`，本機資料與加密金鑰放在 `data/`；不得提交 token、`.env`、內部網址或真實對話樣本。測試 fixture 使用假值。保留以 Viewer 本人 OAuth 身分操作；儀表板與 CLI 送出前須二次確認。`core/polishers/sepia_rules/` 為第三方規則，更新時須保留授權與來源版本紀錄。
