---
slug: submit-worklog-to-zplanner
name: 工時草稿送出到 ZPlanner
type: feature
verified_at_commit: bb00e8f
verified_at: 2026-09-14
drift_policy: normal
---

# 工時草稿送出到 ZPlanner

> 把本地的一天份工時草稿送進 ZPlanner，並把回傳的 worklog id 記回草稿，讓重送認得出「這筆已經送過了」。對應 `docs/api-contract.md:672` 列為尚未實作的 `POST /worklogs/{date}/submit`。

## 目標與範圍        <!-- crew:goal owner=spec -->

新增 `POST /api/v1/worklogs/{date}/submit`：把一天份的本地草稿逐筆送進 ZPlanner。
DB schema、client 寫入層、`unknown` 的錯誤語意都已就緒，本任務只補業務邏輯與路由。

- In Scope：逐筆送出、送出前去重、把結果寫回草稿每一筆的 state、鎖定與權限的錯誤分類。
- Out of Scope：`GET`／`PUT /worklogs/{date}`、correction-request、`/zplanner/issues/search`、
  `/zplanner/permissions`、ZPlanner 的 batch 端點、`unknown` 的自動重試。

## 驗收條件          <!-- crew:ac   owner=spec -->

- [x] AC-1 草稿裡有「未指派 issue 且非 skipped」的筆 → 整批擋下回 400，一筆都不送
- [x] AC-2 已經有 `zplanner_worklog_id` 的筆直接跳過，不重打
- [x] AC-3 送出前用 `export_worklogs` 查當天，同 issue ＋ 同 hours 已存在 → 跳過並列入回報
- [x] AC-4 建立成功 → `state=submitted`，寫回 `zplanner_worklog_id`／`zplanner_issue_key`／`submitted_at`
- [x] AC-5 `ZPlannerWriteIndeterminate` → `state=unknown`，不自動重試，不中斷其餘各筆
- [x] AC-6 回應信封 `code != 200` → `state=failed` ＋ `last_error_code`／`last_error_message`，不影響其餘各筆
- [x] AC-7 `failed`／`unknown` 的筆可單筆重打；該日沒有草稿 → 404
- [x] AC-8 每成功一筆立刻落 DB，不等整批跑完
- [x] AC-9 同一天已有 submit 在進行中 → 第二個請求**立刻**回 409，且一筆都不送（不排隊等）
- [x] AC-10 整批超出時間預算 → 停送剩餘、仍回 200，summary 標明未送出筆數，那些筆維持 `pending` 可重打
- [x] AC-11 孤兒回報**不預設成因**（不得寫死「送出期間草稿被重新產生」）

## 決策紀錄          <!-- crew:dec  append-only -->

- D-1 [spec] 範圍判斷：TASK_TYPE=feature、CHANGE_SCOPE=backend-only、FRONTEND_REQUIRED=false（React）、DB_REQUIRED=false（`worklog_draft_entries` 欄位已齊全）、NEW_API=true、EXISTING_API_CHANGE=false｜理由：schema、client 寫入層、錯誤分類都已就緒，缺的只有業務邏輯與路由。
- D-2 [spec] 去重比對 issue ＋ hours，不比對 note｜理由：note 會被使用者編輯過，比對它會漏判而造出重複工時，而重複工時月結後刪不掉｜否決：完全不去重（真實情境一週 22 筆裡有 9 筆是既有的）、比對含 note（漏判的代價不可逆）
- D-3 [spec] 有任何一筆未指派 issue 就擋下整批，不做部分送出｜理由：ADR-0008 規定歸屬由人選；部分送出會讓草稿停在半送出狀態，而使用者看不出哪幾筆送了｜否決：跳過未指派的照送其餘（那天的工時變成不完整且沒有訊號）
- D-4 [spec] 每成功一筆立刻寫回 DB，不等整批結束｜理由：第 N 筆失敗時，前面已成功的幾筆若還沒落地就成為孤兒——重送會再建一次，而原本那筆永遠不會被認領｜否決：整批成功才寫
- D-5 [arch] 純判斷（未指派閘、去重比對、例外→state 分類）進新的 `core/worklog_submit.py`，比照 allocation／clustering／evidence「不碰 DB、不碰網路」；逐筆編排留在 server.py｜理由：去重與分類是這功能裡唯二「算錯不會有人發現」的邏輯，必須單測得到｜否決：整包寫在 server.py（測不到）、讓 worklog_submit 自己 import repository／client（界線守不住）
- D-6 [arch] repository 新增單筆狀態更新，**一筆一交易**，不沿用 replace_worklog_entries 的整批 all-or-nothing｜
  為何這次不照舊：那支回捲是為了「不留下沒有 entries 的草稿」，這裡回捲會把已寫進 ZPlanner 的 worklog id 一起
  抹掉＝孤兒工時（D-4）｜守門條件（viewer_id、draft_id、現有 id 為空）併進同一句 UPDATE 的 WHERE，不先查再寫；
  影響 0 列＝這筆在送出期間被重產清掉，當孤兒回報｜否決：先 SELECT 再 UPDATE（TOCTOU）
- D-7 [arch] 只要送出任何一筆就回 HTTP 200（即使有筆失敗），body 回重讀後的完整 entries[] ＋ summary 計數｜理由：
  4xx 會讓前端以為整批沒送、使用者再按一次＝ZPlanner 重複工時｜summary 鍵名沿用週檢視的 submitted_count／
  failed_count／unknown_count，兩個畫面才講同一套話；錯誤信封只留給整批拒絕（AC-1 的 400、AC-7 的 404）｜
  否決：207 Multi-Status（既有 api-contract 沒有這一類）
- D-8 [arch] AC-7 走同一支端點、body 加選填 `entry_ids`，不另開路徑｜理由：重打與整批流程完全相同，另開等於把
  同一段寫兩次；api-contract 慣例本來就用 body 欄位表達變體（draft/stream 的 force）｜連帶：帶 entry_ids 時
  AC-1 的閘**只檢查選中的那幾筆**，否則當天別處有一筆沒選專案，failed 的那筆就永遠重打不了
- D-9 [arch] 補三處 AC 沒定義的狀態語意：(a) `export_worklogs` 失敗 → 整批中止，不得「查不到就當沒重複照送」
  （D-2 的代價不可逆）；(b) AC-3 跳過的筆 `state=skipped`，但**不**把比對到的遠端 id 寫進 `zplanner_worklog_id`
  ——那筆不是我們建的，認領它會讓 D-2 自承的誤殺變成永久且看不見，改由回應列出；(c) `worklog_drafts.status`
  要等「每筆都 submitted 或 skipped」才轉 submitted，否則週檢視的 `totals.days_submitted` 永遠是 0
- D-10 [review] 並行防護用**非阻塞的 per-(viewer_id, work_date) 行程內鎖 ＋ 回 409**，不新增 DB 認領狀態｜
  理由：(i) 部署模型是一人一部署、單 worker uvicorn（`server.py:4494`），行程內鎖涵蓋得住；(ii) 非阻塞是關鍵
  ——排隊等最長 90 秒等於把「等太久再按一次」原地放大；(iii) 孤兒 reason 一併改成不預設成因｜
  否決：阻塞式鎖（使用者等更久、更想再按）、DB `state='sending'` 認領（行程崩潰會讓那些筆永久卡住，
  需要另做回收機制——新增一個失敗模式來防另一個失敗模式）｜
  ⚠️ **已知邊界**：多 worker／多行程部署時這道鎖失效，那時必須改成 DB 認領。要寫進 docstring。
- D-11 [review] 整批時間預算 90 秒，到點停送剩餘並回 200｜理由：`(5, 20)` 是 connect 與**每次 read** 的上限，
  不是單次請求總長更不是整批；N=10 最壞 ≈275 秒，對方慢速吐 chunk 則無上限｜
  否決：縮短 read timeout（只會讓首 byte 慢的正常請求變 `unknown`，而 `unknown` 要人工對帳，比沒送出更糟）

## 已知取捨與風險    <!-- crew:risk append-only -->

- `core/repository.py` 目前只有整組取代，沒有單筆 UPDATE，送出要新增（留給 db／arch pass）
- `is_locked` 目前全庫恆 0，submit 是第一個賦予它語意的地方
- AC-3 的去重會誤殺「同一天、同一 issue、同時數」的第二筆（合法但罕見）。取捨是寧可漏送也不要重複，
  因此被跳過的筆**必須列進回報**讓人看得見，不可以靜默跳過
- `is_locked` 直接抄 `create_worklog` 回應的欄位；「送出時沒鎖、之後月結才鎖」本端點看不到，靠對帳補
- 逐筆一交易＝N 次提交，整批不再有一致快照：跑到一半的草稿是混合狀態，前端必須畫得出來
- export 一天只查一次（非逐筆），但它回 CSV 字串且 client 刻意不轉型，`hours` 兩側要正規化到同一刻度再比，
  否則 `"2"` 與 `"2.0"` 會漏判而造出重複工時
- 未涵蓋：`unknown` 的自動對帳（Out of Scope）、`entry_ids` 指到別份草稿的 id（回 400 不是 404）
- D-10 的鎖只在單一行程內有效。多 worker 部署時兩個請求會各自拿到自己的鎖，防護等於不存在——
  這是**已知且刻意**的取捨，不是疏漏；要多 worker 就得先換成 DB 認領
- AC-10 停送的筆維持 `pending`，與「從未送過」無法區分。使用者只能從 summary 的未送出筆數看出來

## 指路              <!-- crew:map  append-only -->

- 既有：`@code:core/db.py#worklog_draft_entries` (L343) state 五態與 partial unique index、`@code:core/errors.py#ZPlannerWriteIndeterminate` (L429) unknown 的來源、`@code:core/zplanner_client.py#create_worklog` (L603)／`#export_worklogs` (L537) 去重與對帳的唯一管道、`@code:core/repository.py#replace_worklog_entries` (L1662) 整組取代
- 既有範本：`@code:dashboard/api/server.py#get_worklog_week` (L3454) summary 鍵名、`#zplanner_client` (L3272) client 取得、`#_refuse_destructive_regeneration` (L3787) 守門 helper、`#_dm_fill_lock` (L206) 行程內鎖慣例
- 純判斷層（D-5，不 import repository／client）：`@code:core/worklog_submit.py#select_entries` (L187)、`#find_unassigned` (L239)、`#find_missing_hours` (L287)、`#find_duplicates` (L331)、`#classify_write_error` (L451)、`#derive_draft_status` (L495)
- 寫入與編排：`@code:core/repository.py#update_worklog_entry_state` (L1762) 單筆一交易（D-6）、`@code:dashboard/api/server.py#_refuse_incomplete_worklog_entries` (L4092) AC-1 閘、`#_submit_one_worklog_entry` (L4253) 逐筆落地（D-4）、`#submit_worklog` (L4379) 路由本體
- 並行與預算（D-10／D-11）：`@code:dashboard/api/server.py#_claim_worklog_submit` 非阻塞互斥、`#_warn_if_multi_worker` 多 worker 警告、`WORKLOG_SUBMIT_BUDGET_SECONDS` 整批預算
- 測試 `tests/unit/test_worklog_submit.py`、`tests/unit/test_worklog_endpoints.py`；契約 `@code:docs/api-contract.md` submit 節

⚠️ `check-spec-drift.py` 對 `zplanner_client`／`get_worklog_week` 會誤報行號（子字串比對，命中 `import` 行與 docstring 提及，實際定義在 L3272／L3454）；**這兩條別跑 `--fix`**。

## 檢查報告摘要      <!-- crew:rep  append-only -->

- [2026-09-14] review FAIL｜🔴2 🟡2｜AC-1～8 與風格全過，但規格從未處理並行：兩次重疊 submit 已實測造出重複工時且先回的那個看不見；整批無時間預算
- [2026-09-14] fix PASS｜🔴0 🟡0｜bb00e8f 修完兩個 🔴 與兩個 🟡，補 AC-9～11／D-10～11；獨立審查員用自己的探針驗過修復版（2 次）與負對照（4 次）
