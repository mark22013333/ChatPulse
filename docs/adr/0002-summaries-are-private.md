# Summary 私有於產生者，即使兩人同群

每一份 **Summary** 綁定產生它的 **Viewer**，其他人看不到，即使雙方都是該 **Space** 的成員。`summaries` 表帶 `owner_viewer_id`，所有查詢一律帶 `WHERE owner_viewer_id = <當前 Viewer>`。

原因是 Google Chat 的成員身分是 ChatPulse 唯一的授權依據，而**摘要一旦寫進本機資料庫，就脫離了那層保護**。若團隊共用的儀表板讓所有登入者都讀得到所有摘要，它就成為一條繞過 Google Chat 權限的資料通道——沒加入「北市府新案」的同事，會在儀表板上讀到那個群組的客戶名、報價與卡關細節。

## Considered Options

- **按 Space 成員資格共享**（讀取前用該 Viewer 自己的 token 驗證他真的在該群）：能省下重複的 Gemini 呼叫，但需要在每次讀取時驗證成員資格，且「人離開群組後舊摘要如何處置」沒有乾淨的答案。
- **團隊全開**：實作最省，但正是上述的資料通道問題。

## Consequences

- 同一個群組裡的兩位 Viewer 會各自花一次 Gemini 額度產生內容雷同的摘要。這是刻意接受的浪費。
- 要分享得靠手動——複製 Markdown 或貼回群組。
- 這條規則只有一個執行點（查詢的 `WHERE` 條件），漏掉一次就等於全開。新增任何讀取 `summaries` 的程式碼時必須檢查。
