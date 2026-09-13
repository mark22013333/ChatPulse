# 以工時草稿取代 v1 的剪貼簿式 ZPlanner 匯出

Draft Worklog 從 Viewer 的 git commit 與 Google Chat 發言產生**每日工時草稿**，由 Viewer 逐筆確認後以他本人的身分寫回 ZPlanner。專案歸屬**一律由 Viewer 在確認畫面選**，系統只給建議與信心度；時數由 Viewer 輸入當日總數，系統按證據比例分配、湊成 0.5 的倍數，Viewer 再微調。

這與 `SPECIFICATION.md` 5.3 與 Won't Have 清單裡那個「已於 v2.0 移除」的 ZPlanner **不是同一件事**，所以要先說清楚差別在哪，否則這份 ADR 讀起來就是在推翻一個才剛下的決定。

## v2.0 當初移除的是什麼

v1 的「ZPlanner 整合」在文件裡有四種互斥的定位（架構圖上的外部服務、前端格式化器、UI 按鈕、後端 API 端點 `POST /api/v1/action-items/export-zplanner`），而**實作只有一段前端 handler**：把 Action Items 組成 `【空間名】內容 (工時: 1.0h)` 字串丟進 `navigator.clipboard.writeText()`。它從未送出過任何一個 HTTP 請求，Phase 1 重寫前端時沒被搬過去，v2.0 於是把它列進 Won't Have（見 `SPECIFICATION.md` 5.3 與附錄 B）。

**那個決定沒有錯，而且現在也仍然成立**：定位講不清楚、又只是剪貼簿的假整合，不值得保留。真正被移除的是「一個講不清楚自己是什麼的功能」，不是「與工時系統往來」這件事本身。

## 為什麼現在做，而且是不同的東西

差別在**有沒有承擔真正的責任**。剪貼簿版本把所有判斷都推回給人：哪個 issue、幾小時、寫什麼，全部自己想，系統只省下打字。Draft Worklog 承擔的是**回想**——「上週三我到底在幹嘛」這件事，證據其實都在（commit 的作者信箱與時間、Chat 上的發言與 Mention），只是分散在沒有人會回頭翻的地方。

它沿用 Draft Reply 已經驗證過的隱喻：**系統產草稿、人逐筆讀過確認、以本人身分送出**。這個隱喻在 Draft Reply 上成立的理由（AI 負責把散落的脈絡收攏成一份可以改的初稿，人負責決定要不要送）在工時上完全一樣，而且工時的容錯更低——填錯的工時會進到別人的專案成本裡。

## 專案歸屬不讓模型決定

這是本篇唯一的硬規則，與 ADR-0003、ADR-0006 以及 `core/db.py:196` 的註解同一條哲學：**由人指定，系統不自動發現。**

模型可以從 commit 訊息與 repo 路徑猜出「這大概是 A 專案」，而且多半會猜對。問題在猜錯的那次長得跟猜對的一模一樣：一份填好的工時草稿，附著看起來合理的 issue 與 note。ZPlanner 的工時會進月結、會影響專案成本，**而月結之後的工時不能直接改，要走 correction-request 流程**。一個「多半正確」的自動歸屬，代價由發現錯誤的人在一個月後支付。

所以系統的輸出是「候選清單 ＋ 信心度 ＋ 證據」，選擇權在 Viewer。這與 Draft Reply 讓 Viewer 勾選 Reference Space、ADR-0006 讓 Viewer 指定專案與環境，是同一個模式的第三次套用。

## 這支 API 有一件事違反所有人的直覺

ZPlanner 的 **HTTP 狀態碼恆為 200**，成敗在 response body 的 `code` 欄位（200／400／401／403／404）。任何用 `raise_for_status()`、`if resp.ok`、`curl -f` 判斷成敗的程式，會把權限不足與 token 失效**全部當成功**，然後帶著 `data: null` 往下跑，過程中不會有任何跡象。

這件事被寫進三個地方，因為它是那種「知道的人不會再錯、不知道的人一定會錯」的事實：`core/zplanner_client` 的模組 docstring、`classify_zplanner_error` 的 docstring，以及 `tests/unit/test_zplanner_client.py` 一整套會在退化時轉紅的測試。與 ZPlanner 往來一律經過 `core/zplanner_client`，不要在別處自己打 HTTP。

## Considered Options

- **維持 v1 的剪貼簿匯出**：零風險、零維護。但它解決的是打字，而打字不是這件事的難處——難處是回想，以及回想錯了要在月結後付代價。
- **自動判斷專案並直接寫入 ZPlanner**：省掉確認步驟，但這正是上面整段在拒絕的東西。而且它與 5.4「任何送出動作都需二次確認」直接衝突，那條規則的適用範圍本來就不限於 Chat。
- **只讀不寫（產出草稿讓人自己貼進 ZPlanner）**：比剪貼簿版好，因為證據蒐集與比例分配仍然成立。這其實是個合理的降級路徑——若寫入端點的權限在某些專案打不開（ZPlanner 的權限是每個專案各自一張角色矩陣，由該專案 PM 設定），就退回這條。

## Consequences

- **多一個外部服務依賴，而且是內網的。** `core` 從此會在 ZPlanner 不可達時需要乾淨降級。設定（`ZPLANNER_BASE_URL`、`ZPLANNER_APIKEY`）兩個值都沒有 fallback 預設值，沒設時 ZPlanner 相關功能回一句可讀的中文，不影響其他功能。
- **這個 repo 是公開的，所以內部系統的位置不進版控。** 處置與缺陷 D-2（Gemini 金鑰曾被硬編碼成 fallback）一致，見 `SPECIFICATION.md` 3.3。
- **token 目前是單一一把，代表「跑這個服務的那個人」。** 專案的散佈方式是每位同事各自 clone、各自部署、各自設自己的 token，所以單 token 與「以 Viewer 本人身分」不衝突——一個部署就是一個人。若將來改成共用部署，`ZPlannerClient` 的 `token` 已經是建構子參數，不必重構這一層。
- **證據來源會把 git commit 帶進這個功能。** 與 ADR-0006 的資料邊界問題同型，但輕得多：這裡只用 commit 的**作者信箱、時間與訊息**來回想做過什麼，不讀程式碼內容、不進 prompt 給外部 AI 供應商。
- **目前只有 client 層（`core/zplanner_client.py`）。** 證據蒐集、比例分配、確認畫面、寫入端點都還沒有。在那些完成之前，`SPECIFICATION.md` 的功能描述不應該宣稱 Draft Worklog 可用。
