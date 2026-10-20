# 驗證離線代理執行

此版本支援 POSIX 平台的 macOS 與 Linux，以及本機 Unix Docker endpoint。程序群組清理依賴 `os.killpg`。Windows launcher 尚未提供，CLI 會回報 `blocked`，不得降級成主機裸 adapter。

從儲存庫根目錄執行預設診斷。

```bash
.venv/bin/python scripts/probe_agent_runtime.py
```

CLI 以真 stdio 子程序檢查合成 binding、generation 更新、ACP 完成、取消，以及真正的 HTTP MCP 查詢與範圍拒絕。預設不啟動 Docker、不讀取主機 CLI 登入，也不呼叫真實模型或 Google Chat。版本與讀取隔離在這個模式標為 `unverified`。

建置固定版本 image。

```bash
docker build --iidfile /tmp/chatpulse-agent-image-id scripts/agent-runtime
```

建置會下載 npm 套件。Dockerfile 固定 Node base digest，package-lock.json 固定 adapter 2.2.2 與 Codex 0.163.0，並停用安裝腳本。`.dockerignore` 只允許診斷所需的四個檔案進入建置內容。

以不可變 image ID 執行容器診斷。

```bash
.venv/bin/python scripts/probe_agent_runtime.py \
  --image-id "$(cat /tmp/chatpulse-agent-image-id)" \
  --docker-executable /absolute/path/to/docker
```

CLI 從可信 Docker CLI 的目前 context 取得本機 unix 引擎端點。啟動與清理都固定使用該端點，環境不繼承主機 API key、proxy 或其他設定。每次執行先 `create`，取得 container ID 後才 `start`。建立途中遭取消而無法確認 ID 時，清理收據保持未確認。

容器使用 `network none`、唯讀根目錄、非 root 身分、run 專用 tmpfs、`cap-drop ALL`、`no-new-privileges` 及 PID 128、記憶體 512 MiB、CPU 1 核心上限。沒有 host repo、config、data 或 Docker socket 掛載。image 內的假 Responses 服務只監聽容器 loopback。

容器診斷會比對實際套件版本，完成固定 marker。HTTP 400 須取得假模型已收到請求及送出 400 的固定事實、`Failed(AGENT)` 與回收確認才通過。取消須先等假模型收到請求，再計時取消並確認容器移除，啟動途中取消不算通過。固定合成事實以私有 `_chatpulse/probe` 通知交付，只接受目前 `DockerProbe` 模式的固定 stage，不公開 stderr 或任意 metadata。

canary 模式由假模型強制要求真 adapter 的內建 shell 工具執行讀取。診斷先在 `/tmp` 建立專用目錄與假檔，確認父路徑可遍歷，目錄為 0755、假檔為 0444。私有診斷 subclass 只在 `isolation_control` 正對照以唯讀 bind 掛載這個假檔到相同絕對路徑，須同時讀到 `HOST_READABLE`、`SYMLINK_READABLE` 與 `SYNTHETIC_HOST_SECRET`，且正常隔離判準須拒絕此結果。這是刻意可讀的診斷例外，不是安全 runtime；不掛載真實檔案、目錄或 socket。

正式 `AgentRuntime` 與 `DockerProbe` 仍不提供 host mount 參數或 fallback。`isolation` 再用原 launcher、相同 image 與 UID 1000 執行無掛載反對照，須讀到容器內正對照，並確認主機假檔、符號連結、ADO 環境與 Docker socket 都不可取得。可讀正對照失敗時，整體隔離不能通過。通過只證明這個 image 與 launcher 的合成測試，不證明正式來源或網路出口已接通。

2026-10-20 合成實測，原本 PID 上限 64 會讓真 Codex 的內建 `exec_command` 間歇回傳 EAGAIN。失敗時 `pids.current` 為 64，工具呼叫前後 `pids.events` 的 `max` 從 14 增至 22。cgroup PID 計數包含原生執行緒，因此將上限調整為有限的 128，其餘隔離與資源限制維持原值。canary 結果的 `pids.before_tool` 與 `pids.after_tool` 各保留一筆同步讀取的 `pids.current`、`pids.max`、`pids.events`，不另啟動 shell 量測。`max` 事件增加代表碰到上限；工具失敗仍使診斷失敗，不加入重試。

CLI 以 JSON 分項回報 `pass`、`failed`、`blocked` 或 `unverified`。有 `failed` 時退出碼為 1。`blocked` 與 `unverified` 不會偽裝成成功，也不表示正式功能可以啟用。

## 呼叫 runtime

`core/agent_runtime.py` 定義公開型別，`core/acp_runtime.py` 的 `AgentRuntime` 擁有 binding、lease、MCP 能力與程序生命週期。這是獨立診斷 runtime，既有 AIProvider 與 Draft Reply 入口尚未啟用它。

可信登入入口提供 `CredentialSnapshot`。主機 CLI 登入與 email 都不能建立 Viewer binding。`issue(viewer_id, scope)` 簽發一次性 lease，`run(lease, prompt, emit=..., cancelled=...)` 在第一個 await 前 claim。重建或重複使用 lease 都遭拒絕。更新憑證撤銷舊 generation，不重播 prompt。`revoke_viewer()` 同時撤銷現有執行與未來簽發資格，必須重新提供可信 binding 才能繼續。

同步 claim 進入 `CLAIMED`，awaitable 真正開始執行並建立 supervisor owner 才進入 `RUNNING`。啟動前取消或未 await 的 claim 在到期或撤權後，可由下次 `issue()` 回收容量。已有 owner 的執行必須等 supervisor 完成清理才能釋放容量。舊 claim 即使稍後才 await，也會在啟動 MCP、子程序或容器前檢查授權，回傳 `Failed(DEADLINE)` 或 `Cancelled` 與已確認的清理收據，不恢復 lease 或重播 prompt。

`emit` 必須是 async callback，與呼叫端在同一個 event loop，由獨立 dispatcher 消費有界 queue。正常完成會交付 queue 中所有文字，總 lease deadline 包含這段等待。外部取消與 callback 例外由 supervisor 獨立處理。callback 或取消訊號 awaitable 不合作時，runtime 有界返回並標示 `cleanup.confirmed=False`。

結果以 `Completed`、`Failed` 或 `Cancelled` 區分，每份結果都包含獨立清理收據。`Completed` 只代表代理回合完成，不代表文字已查證、已獲 Viewer 核准或已送出。清理無法確認時，完成候選改為 `Failed`。取消仍保持 `Cancelled`，清理失敗由收據揭露。

ACP1 的 framing、request ID、session ID、核心事件 schema 與輸出預算在邊界驗證。固定 adapter 2.2.2 的 `jetbrains.air` 版本 1 `sessionFailure` 擴充同時用於 `session_info_update` 與結果 `_meta`。嚴重度 `error` 必須失敗，即使 adapter 回 `end_turn` 且先前已有文字。`warning` 不替代模型產出。升級 adapter 時必須重跑這些正反對照。

## 尚未接通的項目

正式 SIWC registration、真人 Viewer binding、access token 更新及受控網路出口保持 `blocked`。`CredentialSnapshot` 的合成測試不能替代身分驗證。

host fixture 的 MCP wire 已驗證。`network none` 容器無法直接連到 host MCP，容器 MCP 接線保持 `unverified`。正式來源與 SourceBroker 屬後續工作，這個 runtime 不啟用 Chat、ADO 或主機 repo 存取。
