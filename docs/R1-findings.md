# R-1 實測結果：Mention 採集器走實作 B

> 實測日期：2026-09-05
> 對應規格：[`SPECIFICATION.md`](../SPECIFICATION.md) 6.2、十二節 R-1
> 帳號：`markcheng00806@intumit.com`（Google Workspace，436 個 Space）
> 原始輸出：本文件所有數字皆來自當日實跑，探測腳本與 JSON 原始回應見文末

## 結論

**實作 A（`spaces/-/messages:search` 跨群搜尋）不可用；採集器走實作 B（逐群輪詢）。**

但實作 B 的成本遠低於規格書 6.2 的估算——**每輪只需個位數次 API 呼叫，不是 436 次**（多次實測落在 2~3 次），因此
30~60 秒的輪詢間隔不需要放寬。詳見下方「實作 B 的兩個槓桿」。

## 實作 A 為什麼不可用：回 200 但恆 0 筆

這個端點的失敗形態不是權限錯誤，而是**安靜地回傳空結果**，因此特別容易誤判為
「目前沒有人 @ 我」。三層證據：

| 測試 | filter | 結果 |
| :--- | :--- | :--- |
| 版本等級是否受限 | `annotations.user_mentions.user.name:"users/{我}"` | **HTTP 200**，非 403 → 端點與帳號等級可用 |
| 正對照（關鍵） | 同上，但先在暫存群組發一則 `<users/{我}>` 訊息 | 該訊息 annotation 確實含 `USER_MENTION`／`MENTION`，**搜尋 30 秒內反覆查 6 次都是 0 筆** |
| 排除 mention 條件本身 | `space.name = "spaces/AAAAxLxqJxY"` | **0 筆**——連「這個群組的所有訊息」都搜不到 |

第三列排除了「filter 寫錯」與「真的沒有 Mention」兩種解釋：一個確定有數百則訊息的
Space，用官方文件列出的 `space.name = ` 語法去搜也是 0 筆，代表這個帳號的搜尋索引
本身沒有內容。且每一次都回傳 `nextPageToken`，往下追 15 頁仍全部是空的——
**分頁 token 的存在不能當作「還有資料」的訊號**。

### 附帶測出的 filter 語法事實

官方文件列出 `createTime` 支援 `<` 與 `>=`，但在此帳號上**任何含 `createTime` 的
filter 一律回 400 `Invalid filter query`**，連單獨使用、加括號、改小數秒、改時區位移都一樣。

| filter | 結果 |
| :--- | :--- |
| `annotations.user_mentions.user.name:"users/{ID}"` | 200（`:` 運算子，不可用 `=`） |
| `sender.name = "users/{ID}"` | 200 |
| `space.name = "spaces/XXX"` | 200 |
| `createTime >= "..."`（單獨或 AND 組合、任何格式） | **400** |
| `text:"關鍵字"` | 400（`text` 不是合法欄位，自由文字要裸寫） |
| `is_unread()` | 403，需 `chat.users.readstate.readonly` scope |
| `orderBy` | 只接受 `createTime DESC`／`createTime desc`，`ASC` 與裸 `createTime` 皆 400 |

## 實作 B 的兩個槓桿（把每輪成本從 436 次降到個位數）

規格書 6.2 的估算「一輪約 2 分鐘、吃掉 48% 配額」建立在「每輪掃全部 436 個 Space」的
前提上。實測發現兩個可以拿掉這個前提的事實：

### 槓桿一：`spaces.list` 回傳 `lastActiveTime`，且活躍 Space 極少

`pageSize=1000` 一頁就取回全部 436 個 Space，**436/436 都帶 `lastActiveTime`**。
活躍分佈：

| 區間 | 有活動的 Space 數 | 佔比 |
| :--- | :--- | :--- |
| 近 1 小時 | 2 | 0.5% |
| 近 24 小時 | 13 | 3.0% |
| 近 7 天 | 26 | 6.0% |
| 近 30 天 | 38 | 8.7% |

所以每輪只需要輪詢「`lastActiveTime` 晚於上次輪詢時間」的那幾個 Space。
以 45 秒間隔計，多數輪次的候選集合是 **0~2 個**。

### 槓桿二：`spaces.messages.list` 的 `createTime` filter 可用

與 search 端點相反，list 端點**接受** `filter=createTime > "..."`（僅 `>`，
`>=` 回 400）。所以候選 Space 的查詢只取回上次輪詢之後的新訊息，而不是最近 N 則。

### 實測成本

| 項目 | 實測值 |
| :--- | :--- |
| 每輪固定成本 | 1 次 `spaces.list` |
| 每輪變動成本 | 每個活躍 Space 各 1 次 `messages.list`；數量隨當時活躍數而變，實測多為 1~2 |
| 每分鐘配額佔用（45 秒間隔、每輪 3 次計） | 約 **0.4%**（規格書原估 48%） |
| 首次全量掃描（436 個 Space、16 併發） | **外插推算約 18 秒**（8 併發約 33 秒）——實測只跑了 30 個 Space（1.26／2.28 秒），全量未實跑 |

因此規格書 6.3 的「30~60 秒一次」維持不變，不需要改成 2 分鐘。

## 對規格書的修訂建議

| 位置 | 原文 | 應改為 |
| :--- | :--- | :--- |
| 6.2 實作 A | 「首選」 | 標記為**實測不可用**（回 200 恆 0 筆），保留程式碼但預設不啟用 |
| 6.2 實作 B | 「退路」、「撐不起 30~60 秒間隔」 | 改為**正式採用**；補上 `lastActiveTime` 預篩與 `createTime` filter 兩個槓桿 |
| 十二節 R-1 | 「未確認、Phase 2 第一個工作項」 | 改為**已實測結案**，結論指向本文件 |

## 原始證據

探測腳本與完整 JSON 回應保存於本次 session 的 scratchpad：

- `probe_r1.py` — 取得自身 user id（`users/109827265019732088641`）＋ search 端點首測
- `probe_r1_bisect.py` / `r1_bisect.json` — filter 語法二分測試（10 種變體）
- `probe_r1_deep.py` / `r1_deep.json` — createTime 變體、orderBy、15 頁分頁追蹤
- `probe_r1_control.py` / `r1_control.json` — **正對照**：發出真 Mention 後仍搜不到
- `probe_implb.py` / `spaces_dump.json` — `lastActiveTime` 分佈與併發耗時實測

自身 user id 的取得方式：向暫存群組發一則訊息，從回應的 `sender.name` 讀回
（`chat.messages.create` scope 即可，不需要 `userinfo.profile`）。
`https://oauth2.googleapis.com/tokeninfo` 實測**不回傳 `sub`**，所以正式的身分解析
仍需 `openid`／`userinfo.profile`／`userinfo.email` scope（規格書 4.2 已預期）。
