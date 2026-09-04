# ChatPulse

一個給工程團隊使用的 Google Chat 輔助工具：讓成員查閱自己加入的聊天室、產出結構化摘要，並在被他人 @ 提及時取得一份可修改的建議回覆。

## Language

### 人與範圍

**Viewer**：
登入 ChatPulse 的人。每位 Viewer 以自己的 Google 帳號授權，因此只看得到自己在 Google Chat 中加入的 Space。
_Avoid_: 使用者、User、成員、帳號

**Space**：
Google Chat 中的一個聊天室，可能是群組、私訊或討論串。ChatPulse 從不主動加入 Space，只透過 Viewer 既有的成員身分讀取。
_Avoid_: 群組、聊天室、Room、Channel、頻道

**Space 成員身分**：
某位 Viewer 在某個 Space 中的參與資格。它是 ChatPulse 唯一的授權依據——ChatPulse 不另設權限系統，讀得到什麼完全由 Google 決定。
_Avoid_: 權限、Permission、ACL

### 產出物

**Summary**：
針對單一 Space 一段區間的對話所產生的結構化摘要。每份 Summary 歸屬於產生它的 Viewer，其他 Viewer 看不到，即使雙方在同一個 Space。
_Avoid_: 報告、Report、紀要

**Mention**：
一則 Space 訊息中對某位 Viewer 本人的 @ 標記。指的是人被提及，與「Bot 被 @」是兩件不同的事。
_Avoid_: 通知、提及、Notification、At

**已處理**：
一則 Mention 的終結狀態。只有兩種方式進入：Viewer 透過 ChatPulse 送出了回覆，或 Viewer 手動標記。在別處（手機、Chat 網頁）回覆不會讓它變成已處理。
_Avoid_: 已讀、完成、Done、Resolved

**Draft Reply**：
針對某一則 Mention 產生的建議回覆文字。它永遠是草稿——必須經 Viewer 閱讀並確認後才會送出到 Space，且送出以 Viewer 本人的身分為之，不以機器人身分。
_Avoid_: 回應、Answer、自動回覆

**Reference Space**：
產生 Draft Reply 時，Viewer 手動勾選、用來補充脈絡的其他 Space。存在的理由是：@ 提出的問題，答案經常不在提問的那個 Space 裡。
_Avoid_: 相關群組、脈絡來源
