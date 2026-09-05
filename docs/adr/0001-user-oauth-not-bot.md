# 全部走使用者 OAuth，不以 Chat App / Bot 身分行動

ChatPulse 讀寫 Google Chat 一律使用 **Viewer 本人的 OAuth 憑證**，不使用 app 驗證、不使用 service account，訊息送出後在群組中顯示為 Viewer 本人而非機器人。

> **措辭更正（2026-09-05）：本文原本寫「不註冊 Chat App」，那是不精確的。**
> 官方明寫「To perform create, update, and delete API calls, you must also configure
> the Chat API」——**要用 API 送訊息，就必須在 GCP 專案裡配置一份 Chat app**。
> 本專案確實有一份，名為 `T-Bot`（`user_directory` 表裡有它的紀錄：
> `users/100963859070884172925`，因為它曾被加進「0.暫存」）。
>
> 這個決策真正成立的部分是：**不用 app 憑證驗證、不靠 Chat app 的 interaction event
> 收訊息、不把它當成會說話的機器人**。所有讀寫都以 Viewer 本人身分進行，
> `sender.type` 實測為 `HUMAN`。
>
> 這份設定有一個看得見的後果，見下方 Consequences 的「歸屬標示」。

這是一個違反直覺的選擇——這個 repo 目錄名叫 `google-chat-bot`，而專案裡確實曾規劃 Bot 回推摘要（`SPECIFICATION.v1.md:122`）。原因是 Bot 路徑撐不起核心需求：

1. **Bot 收不到「別人 @ 了你」。** Chat App 的 `MESSAGE` interaction event 只在「Bot 自己被 @」或使用者直接對 Bot 發話時觸發。A 在群裡 @ 了人類 B，Bot 永遠收不到這個事件。而「被 @ 之後產生回覆草稿」正是本專案的核心價值。
2. **Bot 必須先被加進每一個群組。** 這使規格書反覆宣稱的「隨選 100+ 群組」在實務上不成立。
3. 其餘門檻：`chat.app.*` scope 需要 Workspace 管理員一次性核准、需建立 Marketplace 相容的 OAuth client、以 app 憑證讀取時只拿得到公開訊息。

## Consequences

- 不需要管理員核准、不需要 Marketplace 送審、不需要把任何東西加進群組。
- Draft Reply 送出後在群組裡就是「你說的話」——這正是這個功能該有的樣子，PM @ 你之後不該是一隻機器人代你回話。
- 代價：系統以你的身分發話，因此任何送出動作都必須經過二次確認。這是 `SPECIFICATION.md` 7.3「不自動送出」的由來。
- 每位 Viewer 都要各自跑一次 OAuth 授權，無法用一組共用憑證。
- **歸屬標示拿不掉（2026-09-05 查證）**：透過 API 送出的訊息，Chat 會在發送者名字旁邊
  顯示 Chat app 的名稱。官方 `create-messages` 逐字寫著「Chat also attributes the Chat app
  to the message by displaying its name」，UI 上呈現為 `你 [T-Bot] 12:36` 這樣的灰底標籤。
  - **無法關閉**：`spaces.messages.create` 的 request body 沒有任何 attribution 相關欄位，
    也沒有對應的 scope 或 Console 開關。**但要精確地說——這是「官方未提供任何關閉手段」，
    不是「官方明令禁止」**，我們找不到方法，不代表方法不存在。
  - **可以改文字**：Cloud Console → Google Chat API → Configuration → Application info →
    App name（上限 25 字元）。改名不會使既有授權失效（OAuth 文件列出的 refresh token
    失效原因清單裡沒有這一項），同事不需要重跑授權。
  - 唯一能讓 app 名稱不顯示的情境是「管理員權限」模式，但那會改成顯示
    「由組織管理員執行」——比顯示工具名更不像本人，不採用。
  - **這個標籤與本決策的方向相反**：架構上一切以本人身分發話，UI 上卻掛著一個
    看起來像機器人的名字。緩解方式是把 App name 取成一望即知是「某人的工具」
    而不是「一隻機器人」的名字。
