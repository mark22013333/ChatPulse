# 驗證紀錄：2026-09-05 的實跑數字與其證據等級

> 這份檔案存在的理由很具體：`tests/e2e/` 的報告檔會**被下一次執行覆蓋**。
> 2026-09-05 當天 Gemini 免費層的每日 20 次配額用完之後，我重跑了測試，
> 於是原本含有真實數字的報告檔被「配額用盡」的版本蓋掉——結果是規格書引用的
> 數字變成查不到來源。這份檔案把那些數字抄下來並標明證據等級，讓後續的人
> 分得清「有落檔證據」「只有終端輸出」「是推算值」三種不同強度的宣稱。
>
> 獨立審查（同日）正是靠比對落檔證據抓到這個問題的，那個審查是對的。
>
> **2026-09-05 09:30 更新**：後來做了兩件事把大部分 B 級升成 A 級——
> (a) 測試報告改成**雙寫**，一份帶時間戳只增不改（`tests/e2e/e2e_lib.py` 的 `set_report`）；
> (b) 新增 `tests/e2e/check_stored_evidence.py`，**從資料庫既有的產出重新驗證宣稱**，
> 完全不呼叫 Gemini。原本以為要等配額才能補的證據，其實大半早就落在 SQLite 裡了——
> 摘要內容存在 `summaries`、草稿存在 `draft_replies`、用量存在 `token_usage`。
> 這是本次最該記住的一點：**證據不一定要重跑才有，先看看它是不是已經落地了**。

## 證據等級定義

| 等級 | 意思 |
| :--- | :--- |
| **A｜有持久證據** | 有落檔的 JSON／報告檔，或可隨時重跑取得（不依賴外部配額） |
| **B｜只有終端輸出** | 當時確實跑出來過，但產生它的報告檔已被覆蓋，重跑需等配額恢復 |
| **C｜推算值** | 由小樣本外插得到，**沒有實跑全量** |

---

## A 級：可隨時重跑驗證

這些不依賴 Gemini 配額，執行對應測試即可重現。

| 數字 | 內容 | 重現方式 |
| :--- | :--- | :--- |
| **436** | 帳號的 Space 總數（D-1 修復後） | `GET /api/v1/spaces` 的 `total`；或 `tests/e2e/test_phase1.py` |
| **436/436** | 每個 Space 都帶 `lastActiveTime` | 同上，`test_phase1.py` 有斷言 |
| **2／13／26／38** | 近 1 小時／24 小時／7 天／30 天有活動的 Space 數 | `scratchpad/spaces_dump.json` ＋ `probe_implb.py` 的分佈計算 |
| **123／10／113** | 非 MENTION 樣本總數／帶 `user.name` 者（全為 `type=ADD`）／廣播型 | `test_add_annotation.py` 產出的 `add_samples.json` 檔頭統計 |
| **2~3 次／0.9~1.1 秒** | 採集器一輪的 API 呼叫次數與耗時 | `POST /api/v1/mentions/refresh` 的 `stats`，或 `GET /api/v1/me` 的 `collector.last_run_stats` |
| **0.4%** | 45 秒間隔、每輪 3 次呼叫對每分鐘配額的佔用 | 由上一列計算（3 ÷ 900 × 60/45） |
| **43** | `user_directory` 累積的人數（會持續成長） | `SELECT COUNT(*) FROM user_directory` |
| **2 則** | 真實（非測試造出）的待處理工作 Mention | `SELECT * FROM mentions WHERE space_id != 'spaces/AAAAxLxqJxY'` |
| **8 個** | `requirements.txt` 鎖版的套件數 | `grep -c '==' requirements.txt` |
| **46 個** | 前端原始檔數 | `find dashboard/frontend/src -type f | wc -l` |
| **13** | 前端單元測試數 | `npm --prefix dashboard/frontend run test` |
| **HTTP 200／0 筆** | 實作 A 的搜尋端點行為（含正對照） | `scratchpad/r1_*.json`；`docs/R1-findings.md` |
| **1,224／3,439／493** | D-4 三種風格的輸出字數（同一批 50 則對話，唯一變數是 style） | `check_stored_evidence.py` 第 2 節，直接讀 `summaries` 表 |
| **4 組配對** | 7.1 Reference Space 的正負對照：同一則 Mention，不勾參考群組的草稿不含答案、勾了的含答案 | `check_stored_evidence.py` 第 5 節，直接讀 `draft_replies` 表 |
| **5 份 Summary 章節全齊** | 16384 在 30~50 則規模下不截斷 | `check_stored_evidence.py` 第 3 節 |
| **total > prompt+output** | R-2：thinking token 確實存在且計入 | `check_stored_evidence.py` 第 4 節 |
| **MAX_TOKENS／1,966／78／0 章節** | D-3 負對照：483 則對話用 2048 確實被截斷 | `scratchpad/e2e-d3-20260905T090909.md` 與 `-091501.md`（兩次獨立執行，帶時間戳不會被覆蓋） |

---

## B 級：只有終端輸出

**經過 09:30 的補救後，這一級只剩一項**——其餘都已從資料庫或帶時間戳的報告
取得持久證據（見上方 A 級表格）。

> 唯一還缺的是：**483 則對話用 16384 跑出 `finishReason=STOP` 的那一次**。
> 負對照（2048 被截斷）今天重跑成功兩次、已落檔；正對照這半因為配額每幾分鐘
> 只釋放一個名額，而測試會先把名額花在 2048 那半，始終輪不到。
> 已為此加了 `--only` 參數（`test_d3_truncation.py --only 16384`）與量測持久化，
> 下次配額充足時一次就能補齊。
>
> 需要說明的是**這一項的缺口比看起來小**：16384 不截斷這件事，在 30~50 則規模上
> 有 5 份資料庫證據（A 級）；缺的只是「483 則這個特定規模」的那一次。

### D-3（`maxOutputTokens` 2048 vs 16384）

同一份 483 則對話（29,611 字對話文本、29,969 字 prompt），來自「PSTB上班沒壓力之群組」：

| 執行 | maxOutputTokens | finishReason | thoughtsTokenCount | candidatesTokenCount | 輸出字數 | 三個章節 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 第一次（斷言修正前） | 2048 | `MAX_TOKENS` | 1,965 | 79 | 124 | 0／3 |
| 第一次 | 16384 | `STOP` | 2,772 | 1,171 | 1,654 | 3／3 |
| 第二次（斷言修正後） | 2048 | `MAX_TOKENS` | 1,962 | 82 | 90 | 0／3 |
| 第二次 | 16384 | `STOP` | 2,622 | 1,160 | 1,640 | 3／3 |

**兩次執行的數字不同是正常的**（模型每次思考長度不同），但這曾造成一個真實的混淆：
`test_d3_truncation.py` 的程式碼註解引用第一次的 1,965／79，規格書引用第二次的
1,962／82，看起來像同一份「實測」出現兩組互斥的值。**兩組都是真的，只是不同輪。**
規格書現已改為引用第二次並註明輪次。

結論不受這個差異影響：兩輪都是 2048 → `MAX_TOKENS` 且章節全缺、16384 → `STOP` 且章節齊全。

### D-4（三種摘要風格）

同一批 50 則對話（暫存群組）：

| style | 輸出字數 | 含「核心討論主題」 | 特徵章節 |
| :--- | :--- | :--- | :--- |
| `general` | 1,224 | 是 | 脈絡／決議／待辦 |
| `technical` | 3,439 | 否 | 技術問題與症狀／已排除的假設與排查過程／技術方案與取捨／未解的技術問題／待辦 |
| `action_only` | 493 | 否 | 只有待辦 |

（另有一次 30 則的 `technical` 摘要經瀏覽器實測，內容截圖未保留。）

### R-2（token 用量的組成）

D-3 測試的第一次執行，16384 那一輪的 `usageMetadata` 原文：

```json
{"promptTokenCount": 24200, "candidatesTokenCount": 1171, "totalTokenCount": 28143,
 "cachedContentTokenCount": 16363, "thoughtsTokenCount": 2772, "serviceTier": "standard"}
```

用它說明兩件事：`totalTokenCount`（28,143）**不等於** prompt＋output（25,371），
差額是 thinking（2,772）；另有隱式快取 16,363。

> ⚠️ **這組數字在資料庫裡查不到**，因為 `test_d3_truncation.py` 直接打 Gemini，
> 不經過儀表板，所以沒有寫入 `token_usage`。而且 `token_usage` 也**沒有**
> `cachedContentTokenCount` 欄位。想從資料庫觀察同一個現象，用經儀表板產生的紀錄即可
> （例：prompt 4,039＋output 776，total 卻是 7,661）——差額同樣是 thinking。

### 瀏覽器 E2E

以 Playwright 驅動真實瀏覽器對真後端操作，涵蓋登入、436 個 Space 虛擬滾動、
摘要 SSE 串流、Action Items 萃取（3 項）、推播二次確認並實際送達、
Mention 收件匣、Draft Reply（勾選 Reference Space）、送出回話並自動標記已處理。
期間（2026-09-04T18:19~18:24Z）`browser_console_messages` 回報 **0 則錯誤、0 則警告**。
**截圖未保留**，重驗需重跑一次瀏覽器流程。

> 一個需要講清楚的細節：事後在 `.playwright-mcp/` 發現一份 03:20 才寫出的 console log，
> 內容是一則 `ERR_CONNECTION_REFUSED @ /api/v1/mentions`。**那不是 E2E 期間的錯誤**——
> 是我在 03:00~03:20 之間為了驗證修正而反覆重啟服務時，那個還開著的瀏覽器頁面
> 照 45 秒週期輪詢收件匣所產生的。所以「E2E 期間 0 錯誤」這句仍然成立，
> 但它的範圍就只到 18:24Z 那次查詢為止，不能推廣成「這個前端不會產生 console 錯誤」。
>
> 附帶一個未處理的觀察：後端消失時前端只在 console 留錯誤，UI 上是否有明確提示
> 未經驗證。這不在規格的驗收條件內，列在此處備查。

---

## C 級：推算值，未實跑全量

| 數字 | 實際測了什麼 | 為什麼是推算 |
| :--- | :--- | :--- |
| **全量掃描 436 個 Space 約 18 秒（16 併發）／33 秒（8 併發）** | `probe_implb.py` 只跑了 **30 個** Space（16 併發 1.26 秒、8 併發 2.28 秒） | 用 `耗時 ÷ 30 × 436` 線性外插。實際全量會受配額限流與長尾 Space 影響，**很可能比 18 秒久**。規格書與 R-1 報告已改用「外插推算」而非「實測」 |

---

## 已修正的兩處誤述

獨立審查抓到的，記在這裡避免再犯：

1. **同一個數字被用在兩個不同的量上。** 「0.91 秒」是**採集器跑一輪的耗時**，
   我卻也拿它寫成「Mention 從發出到入庫的延遲 0.91 秒」。那是兩件事——後者要從
   訊息 `createTime` 量到 `detected_at`，而測試流程是「發訊息 → 手動觸發採集」，
   中間的間隔由測試腳本決定，不是系統延遲。規格書已改為只描述輪詢耗時，
   並把「60 秒內出現在收件匣」的依據改為「輪詢間隔 45 秒 ＋ 一輪約 1 秒」。
2. **把外插值寫成「實測」**（上表 C 級那一列）。

兩者都屬於同一類毛病：**數字是真的，但它證明的範圍比我寫的窄**。
