# 專案回話的模型輔助評估

## 先區分引用與結論

找到相關內容，不代表該內容支持回話。新流程分別評估引用有效性、陳述支持程度、版本時效與問題環境。工單 Done、PR merged、部署完成與症狀解除維持不同事實。介面標示「模型輔助評估，仍需確認」，不將模型自評當成已證實。

## 同意決定內容用途

每題依確切目標選擇來源。metadata_only 不讀內容；analysis_only 只供私有分析與受限補查；reply_context 才能進入對外事實包。對外生成重新使用允許揭露的原始證據，不接收私有分析文字。問題預設脈絡限於 Mention 的討論串。其他 Space 與 PM 須明選。

來源讀取與模型呼叫前後會檢查功能旗標、Viewer opt-in 及 scope。同步供應商無取消 API，因此逾時只撤銷結果採用權，仍占用有限 worker 容量直到返回。晚到結果不能寫入草稿或用量紀錄。

## 收據綁定原樣文字

單一 ReplyPipeline 處理 prepare、review 與 send。生成完成評估後才交付回話。人工改字立即撤銷收據；review 重讀來源，保留人工文字並以遞增 revision 防止舊結果覆蓋新結果。

send 核對 Viewer、Mention、確切目標、scope、文字雜湊、版本、期限及來源 replay。來源變動須重新查核。交易內先登記單次 attempt，再以停用 OAuth 重送與重新導向的 transport 送出。結果不明不自動重試。一般草稿維持既有流程，新模式預設關閉。

## 驗證與限制

後端測試覆蓋私有來源隔離、格式及預算、撤銷同意、人工改字、收據偽造與過期、review 反序、來源變動與單次 dispatch。實際 AuthorizedSession 搭配假 adapter 驗證 401 及重新導向不重播新模式 POST。

`tests/e2e/test_reply_evidence.cjs` 啟動 production dist 靜態頁並攔截全部 API。13 項檢查涵蓋三態來源、環境、改字失效、重查保留原文、未通過禁止送出，以及二次確認後原文與最新收據只送一次。截圖全為合成資料。

獨立註解、正確性與可維護性審查曾找出撤銷同意後繼續模型呼叫、redirect 重播，以及來源雙狀態。修正後由審查者重查。完整決策摘要見 [決策紀錄](reply-evidence-decisions.tsv)，端點細節見 [API 契約](../api-contract.md)。

腳本模型只證明政策接線，不能證明推論準確率改善。圖片與附件內容尚未查核。正式 ACP 認證與隔離來源通道仍未驗收。ADO 目前僅有獨立離線 client，不接本流程。既有 ACP 非零結束測試的偶發失敗另追蹤於 [Issue #9](https://github.com/mark22013333/ChatPulse/issues/9)。
