# 全部走使用者 OAuth，不建立 Chat App / Bot

ChatPulse 讀寫 Google Chat 一律使用 **Viewer 本人的 OAuth 憑證**，不註冊 Chat App、不使用 service account，訊息送出後在群組中顯示為 Viewer 本人而非機器人。

這是一個違反直覺的選擇——這個 repo 目錄名叫 `google-chat-bot`，而專案裡確實曾規劃 Bot 回推摘要（`SPECIFICATION.v1.md:122`）。原因是 Bot 路徑撐不起核心需求：

1. **Bot 收不到「別人 @ 了你」。** Chat App 的 `MESSAGE` interaction event 只在「Bot 自己被 @」或使用者直接對 Bot 發話時觸發。A 在群裡 @ 了人類 B，Bot 永遠收不到這個事件。而「被 @ 之後產生回覆草稿」正是本專案的核心價值。
2. **Bot 必須先被加進每一個群組。** 這使規格書反覆宣稱的「隨選 100+ 群組」在實務上不成立。
3. 其餘門檻：`chat.app.*` scope 需要 Workspace 管理員一次性核准、需建立 Marketplace 相容的 OAuth client、以 app 憑證讀取時只拿得到公開訊息。

## Consequences

- 不需要管理員核准、不需要 Marketplace 送審、不需要把任何東西加進群組。
- Draft Reply 送出後在群組裡就是「你說的話」——這正是這個功能該有的樣子，PM @ 你之後不該是一隻機器人代你回話。
- 代價：系統以你的身分發話，因此任何送出動作都必須經過二次確認。這是 `SPECIFICATION.md` 7.3「不自動送出」的由來。
- 每位 Viewer 都要各自跑一次 OAuth 授權，無法用一組共用憑證。
