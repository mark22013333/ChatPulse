# 交接：接續 ChatPulse 的下一個 session

> 更新於 2026-09-07。這份是給「沒有前一個 session 記憶」的人／AI 看的。
> 只寫**接手時真的需要知道的事**，其餘一律指向對應文件。

> **⚠ 2026-09-10 補充**：介面改版正在分支 `feature/ui-redesign-evidence-first` 上進行，
> 尚未合併回 `main`。**如果你要接的是介面改版**，看 `docs/design/HANDOFF.md`
> （兩個已定位的 bug ＋ 五項未完成工作），設計決策則看 `docs/design/2026-09-09-ui-redesign.md`。
> 這份文件描述的是 `main` 上的狀態，改版分支上有些細節已經不同（例如右欄不再是雜物抽屜、
> 回覆設定已從草稿工作區抽到設定中心）。

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

- **狀態**：規格 v2.0 的 Phase 0／1／2 全部完成，21 條驗收條件逐條有證據。
  之後又加了三件規格書沒寫的事：**圖片支援**、**Windows 支援**、**同事安裝體驗**
- **分支**：`main`，與 `origin/main` 同步
- **服務**：`./chatpulse.sh web` → http://localhost:8000（開發時加 `--dev` 開自動重載）
- **AI 供應商**：`claude_cli`（吃本機 Claude Code 訂閱，預設）與 `gemini`（每天 20 次）。
  另有 `claude`／`auto` 兩個別名。**`claude_api` 已於 2026-09-05 移除**（從未對真實 API 跑過）
- **同事入口**：`./chatpulse.sh`（macOS/Linux）／`chatpulse.bat`（Windows），
  互動式引導，邏輯在 `scripts/onboard.py` 兩平台共用

---

## 先跑這個確認現況

```bash
cd /Users/cheng/google-chat-bot
./chatpulse.sh web              # 另開一個終端

export CHATPULSE_BOOTSTRAP_USER_ID=users/109827265019732088641
.venv/bin/python tests/e2e/check_stored_evidence.py   # 從資料庫重查宣稱
.venv/bin/python tests/e2e/test_providers.py          # AI 供應商切換
.venv/bin/python tests/e2e/test_attachments.py        # 圖片附件與視覺
.venv/bin/python tests/e2e/test_static.py             # 靜態托管、授權迴歸
.venv/bin/python tests/e2e/test_add_annotation.py     # Mention 判定條件
npm --prefix dashboard/frontend run test              # 前端 77 項
.venv/bin/python -m unittest discover -s tests/unit   # 單元 134 項（零 API、零配額、0.03 秒）
node tests/e2e/test_merge_reply.cjs                   # 收件匣多選合併 11 項
node tests/e2e/test_message_preview.cjs               # 最近訊息預覽 21 項（唯讀、零 AI）
```

**跑 e2e 之前先確認 8000 埠上是誰的服務**：那幾支 `.cjs` 會走 `/api/v1/auth/bootstrap`，
而 bootstrap 需要啟動服務時就帶著 `CHATPULSE_BOOTSTRAP_USER_ID`（舊的三 scope token
沒有身分權限）。沒帶的話測試會全部 timeout，看起來像程式壞了。
用 `CHATPULSE_URL=http://127.0.0.1:8010` 另開一個埠跑，不必動你正在用的那個。

**配額說明（原本這裡寫「不消耗任何 AI 配額」，不精確）**：這六套**不動 Gemini
的每日 20 次**，但 `test_providers` 第 3 節與 `test_attachments` 第 6 節各會實跑一次
`claude_cli`，消耗 Claude Code 的訂閱用量。要完全零 AI 呼叫，只跑
`check_stored_evidence.py`（它從資料庫重查既有產出）。

要跑會用到 Gemini 配額的完整套件：`tests/e2e/run_all.py`。

安裝狀態檢查（同事也適用）：`./chatpulse.sh check` 或 `./scripts/doctor.sh`。

---

## 未完成 / 開放中的事

| 項目 | 狀態 | 下一步 |
| :--- | :--- | :--- |
| **看一眼 Space 裡在講什麼，要先跑一次摘要** | **2026-09-07 已實作**：摘要工作台點 Space 就顯示最近 10／20／30 則（可選），討論串標同色左邊框＋徽章，可按「展開整串」補齊被切掉的部分（按了才打 API）。順手修掉 `/api/v1/messages` 會丟掉純圖片訊息的問題 | 若覺得每次點 Space 都打一次 API 太積極，改成按鈕觸發即可（`store/preview.ts` 的 `load`） |
| **對方連問兩件事，草稿只回其中一件** | **2026-09-07 已修**：「要回哪幾則」的判準從「間隔 < 5 分鐘」改成「從我上次發言到現在，對方講了什麼我還沒回」。實測那個案例（相隔 59 分鐘）現在 `anchor_count` 從 1 變 2、兩題都被完整回覆 | 若覺得一次回三件事太長，把 `DRAFT_ANCHOR_MAX_CLUSTERS` 調成 2 |
| **同一個人連問兩件事只能分兩次回** | **2026-09-07 已實作**：收件匣可多選（限同一個 Space；分串聊天室還要同一串），合併成一份草稿、送出一則、一次結掉全部。prompt 加了〈逐則確認〉欄位，漏回一題會被看見 | 觀察合併後的回話會不會太長。真的太長就把 `server.MERGE_MAX`（5）調小 |
| **Draft Reply 的脈絡只有 1 則（私訊）** | **2026-09-06 已實作**（`core/draft_context.py` ＋ 47 項單元測試）。同一個私訊實測：脈絡 1 則→7 則、圖片 1 張→3 張；群組長串驗證與改動前逐則相同 | 觀察一段時間。若「隔很久重問同一件事」常被 48h 上界切掉，把 `DRAFT_WINDOW_HOURS` 調成 168（不要拿掉）；若群組薄串開始張冠李戴，設 `CHATPULSE_DRAFT_CROSS_THREAD=0` |
| **程式碼佐證的前端 UI** | 後端已完成（CRUD 端點 + draft_stream 串接 + `code_meta` SSE 事件），**前端沒有設定頁**，目前只能用 curl 操作 | 做專案設定頁 + 草稿工作區的專案選擇器 + 顯示 `code_meta` |
| **儀表板自動 bootstrap** | 首次進入要手動按「匯入既有憑證」 | 偵測本機有有效 token 就自動匯入 |
| **推播摘要／送出回話的狀態在元件 useState** | 切走頁籤會遺失，有重複送出的風險。短操作，影響小 | 搬進 store（與串流狀態同樣的處理） |
| **`chatpulse.bat` 的 Windows 實測** | 2026-09-06 首次實機跑過，**抓到一個真 bug**：`onboard.py` 的 Windows 分支直接跑 uvicorn、跳過前端建置，使用者一開瀏覽器就撞見「找不到前端建置產物」。已修（見下方「Windows 啟動儀表板」）。**修法本身仍未經 Windows 實機驗證** | 請原回報者再跑一次 `chatpulse.bat web`，確認看到畫面而不是那段文字 |
| **Chat app 名稱要改成工具名** | 原為 `T-Bot`，2026-09-06 一度改成 `MarkCheng`，但那是錯的——App name 是**專案層級共用設定**，取人名會讓同事的訊息顯示成 `王小明 [MarkCheng]` | 改成 `ChatPulse` 之類的工具名。位置見 ADR-0001 |
| **Gemini 的圖片路徑未驗** | `inlineData` 依官方文件實作，但沒對真實 API 跑過（不想燒每天 20 次配額） | 有配額餘裕時送一張圖驗一次；欄位名與大小上限都未實測 |
| **「兩位真人 Viewer 各自 OAuth」未驗** | 只有一個 Google 帳號。授權隔離邏輯已用「資料庫建第二位 viewer + session」走 HTTP 層驗過 | 與上面的 Windows 實測合併做：找同事跑一次完整安裝就同時驗掉這兩條 |
| **D-2：建議更換 Gemini API key** | 程式面已修（無硬編碼）。金鑰本身建議換 | 理由見 SPECIFICATION.md 3.3「憑證絕不進 repo」下方的收斂結論 |
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

### 圖片（2026-09-05 新增）

- **`--input-format stream-json` 強制 `--output-format stream-json`**。所以有圖的
  `generate()` 不能再用 `--output-format json`，實作改成把 `stream_text` 接起來
- **視覺是模型的原生能力，不是 `Read` 工具**。我們把 CLI 的工具全停用了，
  但完全不影響它看圖（實測 init 事件顯示 `"tools":[]` 仍答得出圖片內容）
- **圖片成本約 750 像素／token，而 API 不會替你縮圖**。一張未縮的 1920×1080
  要 2,694 tokens，九張≈一整份 483 則對話的文字量。**縮圖是預設行為不是選項**
- **Gemini 免費層的限制是請求數不是 token**，所以在免費層加圖幾乎免費；
  升付費層後這個結論立刻反轉
- **Drive 來源的圖片下載不到**（需要 Drive scope，本專案沒有），實測約佔全部附件一成。
  這些會標成「存放於 Google Drive，本系統無權讀取內容」而不是靜默略過

### Google Chat API

- **`spaces.messages.list` 預設排序是 `createTime ASC`**（最舊優先）。不帶 `orderBy`
  抓「最近 N 則」會拿到最舊的 N 則——這是 D-6，修復前的摘要都在摘三年前的對話
- **使用者驗證下不回傳 `sender.displayName`**，所有發言者會變「未知成員」（D-7）。
  解法是從 `USER_MENTION` annotation 的 `startIndex`／`length` 反建名錄（`core/directory.py`）
- **`spaces/-/messages:search` 在此帳號回 200 但恆 0 筆**，連正對照都搜不到。
  採集器因此走輪詢（實作 B）。詳見 `docs/R1-findings.md`
- `messages.list` 的 filter 只吃 `createTime >`（`>=` 回 400）；search 端點則
  **完全不吃 createTime**
- **附件下載不需要新增 scope**：現有的 `chat.messages.readonly` 就夠
  （`GET /v1/media/{resourceName}?alt=media`）。但 `resourceName` 要填
  `attachmentDataRef.resourceName`，**不是** attachment 自己的 `name`
- **透過 API 送出的訊息一定會帶「歸屬標示」**（發送者名字旁的灰底 app 名稱），
  官方明說是設計行為，**關不掉**，只能改文字。詳見 ADR-0001
- **`messages.list` 回的是扁平訊息流，討論串回覆混在裡面**，沒有參數可以排除。
  2026-09-06 實測（暫存群組，limit=50）：50 則分屬 38 個 thread，其中 10 個 thread
  有多則，最長一串 4 則。所以摘要**讀得到** thread 回覆——但因為是按 createTime 取
  最近 N 則，**同一串常常只被切到片段**，AI 看到的是斷掉的對話。這是摘要偶爾漏掉
  討論結論的原因，不是 prompt 的問題，調大 limit 才有用。
  對照組：草稿走 `list_thread_messages()`（`core/chat_client.py:331-356`），
  用 `filter='thread.name = "…"'` ＋ 最多翻 50 頁，撈的是**整串**，脈絡一定完整。
  兩者的差異值得記住——同樣叫「讀訊息」，一個是時間窗、一個是討論串。
- **`spaceThreadingState` 不能單獨當「有沒有討論串」的判準**（2026-09-06 實測 436 個
  Space）。官方文件說 `UNTHREADED_MESSAGES` 涵蓋私訊，**實際回傳不是**：

  | spaceType | spaceThreadingState | 數量 |
  | :--- | :--- | ---: |
  | GROUP_CHAT | THREADED_MESSAGES | 180 |
  | **DIRECT_MESSAGE** | **THREADED_MESSAGES** | **131** |
  | SPACE | THREADED_MESSAGES | 98 |
  | SPACE | UNTHREADED_MESSAGES | 27 |

  照文件寫 `spaceThreadingState in (...)` 的話**私訊一則都修不到**——正是要修的那個 bug，
  而且改完看起來像修好了。判準必須與 `spaceType == "DIRECT_MESSAGE"` **取聯集**
  （`core/draft_context.is_flat_space()`）。抽樣佐證：DM 30 則/25 thread、
  SPACE/UNTHREADED 30 則/30 thread（皆扁平）；GROUP_CHAT 兩樣本一個 30/30、
  一個 30/22 最長 8 則（**真的有串**），所以 GROUP_CHAT 走討論串路徑。
  `docs/draft-context-design.md` 的 T-2 建議照文件用 `("UNTHREADED_MESSAGES",
  "GROUPED_MESSAGES")`，那份是**未實測的推測，不要照抄**。

### Windows 啟動儀表板（2026-09-06 實機回報後修正）

- **症狀**：Windows 同事跑 `chatpulse.bat`，瀏覽器開起來卻是一段純文字
  「找不到前端建置產物」。macOS 上怎麼試都正常。
- **根因**：`onboard.py` 的 `_start_dashboard()` 有 `if IS_WINDOWS` 分支——
  macOS 交給 `start-web.sh`（裡面會 `npm install` + `npm run build`），
  Windows 那側只有一行 uvicorn，**整段建置被跳過**。而 `dist/` 當時在 gitignore 裡，
  同事 clone 下來必然沒有產物，所以是 100% 重現、不是偶發。
- **教訓**：這個檔案開頭就寫了「兩份腳本必然漂移，所以邏輯統一放 Python」，
  結果漂移發生在**同一個函式的 if/else 兩側**。把邏輯搬進同一個檔案不等於統一，
  **只要還有 `if IS_WINDOWS` 分開做同一件事，就還是兩份實作**。
- **現在的架構**：`scripts/webapp.py` 是儀表板啟動的唯一實作，
  `chatpulse.sh` / `chatpulse.bat` / `start-web.sh` 全是薄殼。要改啟動流程只改那一個檔案。
- **`dist/` 現在進版控**（`.gitignore` 有 `!dashboard/frontend/dist/` 的例外）。
  改完前端**記得把重建結果一起 commit**，否則同事 clone 到的是舊畫面。
  啟動時會用 `dist/.buildinfo.json` 的來源雜湊比對，過期且有 npm 就自動重建並提醒你。
- **為什麼用內容雜湊不用 mtime**：git clone 會把所有檔案的 mtime 設成 checkout 當下，
  用 mtime 判斷新舊會讓每個同事一 clone 就被要求重建。
- **Windows 上呼叫 npm 必須用 `shutil.which("npm")` 拿完整路徑**：
  npm 實際是 `npm.cmd`，`subprocess` 不套用 PATHEXT，直接傳 `"npm"` 會 FileNotFoundError。
- **輸出緩衝**：stdout 不是終端機時 Python 會整批緩衝，而子行程（pip/npm/uvicorn）
  直接寫 fd，於是引導訊息全部堆到子行程輸出**後面**，順序亂到看不懂。
  `webapp.unbuffer_output()` 在任何輸出前處理掉這件事（順帶把編碼錯誤設成
  `errors="replace"`，免得沒經過 .bat 時 cp950 主控台印中文直接拋例外中止）。
- **`chatpulse.bat` 必須全檔 ASCII，不可加中文註解**（2026-09-06 實機回報）。
  症狀是啟動時先噴幾行 `'cp950，' is not recognized as an internal or external
  command`，而那些字串正是 `.bat` 裡中文註解的**後半段**。
  機制：cmd.exe 用**位元組偏移**記住批次檔讀到哪，但用**當前碼頁**解碼；
  `chcp 65001` 在檔案中間切換碼頁後，後續位元組改以 UTF-8 解讀，字元邊界跟著位移，
  讀取位置就落到某個中文字（UTF-8 佔 3 位元組）的中間，於是註解的後半段被當成
  指令送去執行。ASCII 位元組在 cp950 與 UTF-8 下完全相同，所以 ASCII-only 不會漂移。
  修法是把註解與 `:nopython` 訊息全改英文，中文一律交給 Python 印（它的編碼由
  `PYTHONUTF8` 管，不受這個問題影響）。**這個坑會靜默復發**——任何人日後在
  `.bat` 加一行中文註解就會重現，所以檔案開頭有一段全大寫的警告。
- **`.bat` 必須是 CRLF**。cmd.exe 執行 `goto` 時用位元組偏移量重新定位並逐行重讀，
  對 LF-only 批次檔的處理不穩，而 `chatpulse.bat` 剛好有 `goto` 跳轉、標籤、
  多行 `( )` 區塊三個高風險特徵。原本這件事取決於每位同事的 `core.autocrlf`
  設定（Git for Windows 精靈預設 true 會沒事，改過的人就中獎），
  現在由 `.gitattributes` 的 `*.bat text eol=crlf` 保證。
  實測：clone 出來的 chatpulse.bat 是 81 行全 CRLF。
- **專案路徑不要含 `& | < > ^ %`**。`claude` 在 Windows 上是 `claude.cmd`，
  subprocess 執行 .cmd 會隱式經過 cmd.exe，引數被解析兩次——路徑裡一個 `&`
  就會讓 cmd 在那裡把命令切成兩段（`C:\Users\me\R&D\proj` 這種資料夾名
  在研發單位不罕見）。`onboard.py` 的 `_windows_path_hazard()` 會事先偵測並警告。
- **不要把路徑內嵌進 `python -c` 的原始碼字串**，用 `sys.argv` 傳。
  含單引號的路徑（`C:\Users\O'Brien\…`）會讓探針語法錯誤，而錯誤被
  `capture_output` 吃掉，表現是「AI 供應商」那一項安靜地什麼都不顯示——
  是靜默誤判，不是報錯。

### 這台機器與工具

- **zsh 有 `noclobber`**：`cmd > file` 會失敗，要用 `>|`
- **Bash 工具的工作目錄會跨呼叫保留**：某次 `cd` 到子目錄之後，後續的相對路徑
  （`.venv/bin/python tests/…`）會全部失敗。**這個 session 踩了兩次**。
  跑測試一律先 `cd /Users/cheng/google-chat-bot &&` 或用絕對路徑
- **`gitstatusd` 會製造殘留的 `.git/index.lock`**：git 寫入指令不要接管線
  （`| tail` 會吞掉 exit code），開完分支要用 `git rev-parse HEAD` 比對基準
- **更嚴重的同類坑**：`git checkout main >/dev/null 2>&1 && git merge …` 這種寫法
  會把錯誤**整個吞掉**。這個 session 踩過一次：checkout 成功、merge 失敗，
  只看到 `exit 128` 和「檔案怎麼變回舊版了」（那其實只是切換分支的正常結果）。
  **git 寫入型指令不要吞輸出**，重跑時讓它印出來
- **PreToolUse hook** 會攔截遞迴刪除與**推送到 main**，且是對**整條指令文字**比對——
  commit message 裡提到危險指令的字樣也會被擋。push 要由人執行
- 測試報告寫**兩份**（帶時間戳的那份不會被覆蓋），落在 `tests/e2e/reports/`（已 gitignore）

### 身分與名字

- **user id 不是名字**（2026-09-07 修）。舊的三 scope token 沒有 userinfo 權限，
  `identity.resolve()` 的 fallback 路徑就拿 `users/1098…` 當 `display_name` 回傳。
  後果有兩層：畫面右上角顯示那一串數字；而且登入時的
  `directory.remember(user_id, display_name)` 把同一串寫進**人名名錄**——
  於是對話裡的「我（users/1098…）」也是這麼來的，而那份名錄是摘要與草稿共用的。
  現在三道防線：identity 回 `None`、`directory.remember()` 擋 id 形狀的名字、
  `load_all()` 濾掉資料庫裡已有的髒資料（名錄唯一的出口，濾一次全部乾淨）。
  `_viewer_public()` 讀到髒的會即時查名錄補上並寫回資料庫。
- **名錄通常查得到自己**：只要你曾經在任何群組被 @ 過，
  `learn_from_messages()` 就從 annotation 學到你的名字了（實測 55 個人）。

### Draft Reply 的「要回哪幾則」

- **判準是「我回了沒」，不是「隔多久」**（2026-09-07 修）。原本用「同一人 ＋
  間隔 < 5 分鐘」收攏，理由是私訊常把一個問題拆三則發。但實測踩到反例：
  對方 11:33 問白名單、12:32 問 LINE 推播，相隔 **59 分鐘**，於是只有後面那則
  被標成要回的，前面那則掉進背景脈絡——草稿把 LINE 推播答得很完整，
  白名單那題只寫「我另外看，確認完再回你」。
  現在的規則：從錨點往前後收攏**同一發話者**的訊息，碰到別人講話（包含我自己）
  就停；受 48h 上界與 `DRAFT_ANCHOR_MAX_CLUSTERS`（3 件）限制。
  `DRAFT_ANCHOR_RUN_GAP_MINUTES` 現在**只用來分群**（判斷這是 1 件事還是 N 件事），
  不再決定要回哪幾則。
- **prompt 要明講「不可以只寫我再看看」**。多錨點時模型很容易把其中一題寫成
  「我另外看」就算交差——那等於沒回。現在要求寫出缺什麼／要去確認什麼。

### 驗證方法本身的坑

- **跑「把程式碼改壞看測試會不會紅」的正對照時，要關掉 `.pyc` 快取**
  （2026-09-07 踩到）。`.pyc` 的失效判斷是 `(來源 mtime 秒數, 來源大小)`。
  兩個變異若剛好刪掉**同樣長度**的字元、又在同一秒內接連寫入，第二次會沿用
  第一次的位元碼——測試跑的是上一個變異版，於是回報「改壞了卻全綠」的假結果。
  當時 M6 與 M6b 各刪 24 個字元，M6b 因此被誤判成「沒有測試守著」。
  修法：`python -B` ＋ `PYTHONDONTWRITEBYTECODE=1`，並在每次變異後清掉
  `__pycache__`。**把關工具本身也要有正對照**——那次是因為手動重跑同一條指令
  得到相反結果才發現的。
- **只測 core、不測接線，等於沒守住功能**。同一次正對照發現：把 server 那行
  `self_user_id=viewer.get(...)` 改成 `None`，整條新判準等於沒開，**105 個測試
  照樣全綠**——因為所有測試都直接呼叫 `draft_context.build()`。
  已補 `tests/unit/test_draft_stream_wiring.py`，用 mock 把 `build()` 的呼叫參數
  攔下來驗。凡是「core 有測、但參數要從 server 傳進去」的功能都該有這種測試。
- **截斷輸出會製造假的「實測發現」**。這個 session 踩過：探針印 attachment JSON 時
  截斷在 1200 字元，而 `source` 欄位排在很長的 `downloadUri` 之後被切掉，
  於是誤判成「`source` 不一定回傳」並寫進設計文件與程式碼註解。
  完整掃描 84 個附件後確認**全部都有 `source`**。**印 JSON 做判斷時不要截斷**
- **有些 bug 只有真實使用者才踩得到**。這個 session 有三個是使用者實際去用才發現的：
  精靈的 scope 偵測（要「token 過期＋scope 不足」的特定組合）、
  App name 取成人名（只有多人使用才看得出來）、圖片沒被納入。
  **維護者在自己機器上什麼都是好的**——這是為什麼「找同事實測」不該省

---

## 專案結構

```
chatpulse.sh / chatpulse.bat   同事的入口（互動式引導，薄殼，只負責找 Python）
core/            共用封裝（Google Chat／AI 供應商／SQLite／加密／名錄／採集器／附件）
  providers/     AI 供應商：base 介面 ＋ claude_cli／gemini 兩個實作
  attachments.py 圖片挑選、縮圖、token 預算控管
mcp_app/         MCP 入口（5 個工具）、CLI 摘要、OAuth 授權精靈
dashboard/       FastAPI 後端 ＋ React 19 前端
scripts/         onboard.py（引導邏輯）、webapp.py（儀表板啟動）——兩平台共用這兩份；
                 doctor.sh 與 start-web.sh 是薄殼
tests/unit/      純函式單元測試（不打 API），`.venv/bin/python -m unittest discover -s tests/unit`
                 合併回覆的規則有兩份實作（後端 resolve_merge_targets、前端 lib/merge.ts），
                 兩邊都有測試，**改一邊要改兩邊**
tests/e2e/       八套端對端測試，對真實 API 取證、不用 mock
  reports/       測試報告落點（gitignore）
docs/            api-contract.md（契約）、R1-findings.md、verification-log.md、adr/
config/ data/    憑證與 SQLite，皆不進版控
```

---

## 硬性限制（沿用自最早的 session）

**發送 Google Chat 訊息只能發到 `spaces/AAAAxLxqJxY`（0.暫存）。** 其他 Space 只能讀。
`tests/e2e/e2e_lib.py` 的 `guard_space()` 是硬性 assert，會擋掉任何其他目標——
不要為了方便把它改掉。

（注意：這條限制是**測試腳本**的，儀表板與 MCP 本身沒有這個限制，
使用者可以對任何自己有權限的 Space 發言。）
