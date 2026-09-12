# 四個待改畫面的內容盤點

> 2026-09-12。設計稿要用**真實文案**，不要用 Lorem 或自己編的字——這份就是來源。
> 動態內容標成 `{變數}`。行號是盤點當下的位置，僅供定位。

---

## 1. Mention 收件匣（左欄，`w-inbox`）

`components/MentionInbox.tsx`

**區塊（上→下）**

1. **標頭列**（`:117-131`）圖示 ＋「Mention 收件匣」＋ 右側 ghost 鈕「立即檢查」（進行中換 spinner）
2. **狀態分頁**（`:133-155`）「待處理」後接 signal 底圓形計數 `{pending}`（0 則不畫）／「已處理」後接灰色 `{resolved}`（恆顯）
3. **錯誤條**（僅有錯時，`:158-163`）destructive 底 ＋ `{error}`
4. **合併列**（僅勾選時，`inbox/MergeBar.tsx:23-38`）
   - 「已選 {n} 則，一起回成一則」／「取消」
   - 主鈕二選一：「再勾一則才需要合併」（n<2，disabled）或「合併產生草稿（{n} 則）」
   - 註腳：「回話會送到「{space_name}」，送出後這 {n} 則會一起標成已處理。」
5. **清單／空狀態**（`:178-220`）載入中「正在載入…」；空狀態依分頁「沒有待處理的 Mention。」／「還沒有已處理的 Mention。」
6. **卡片**（`inbox/MentionCard.tsx:52-132`）
   - checkbox ＋ `{space_name}` ＋ 右上相對時間（「3 小時前」）
   - `{sender_display} 提到你`
   - 3 行截斷內文；取不到時「（無法取回訊息內容：{content_error}）」或「（訊息內容取不到）」
   - 不可勾時 caution 色一行原因（`lib/mergeCopy.ts:19-23`）：
     「不同的聊天室，沒辦法用一則回話回完」／
     「同一個聊天室但不同討論串——回話只會送到其中一串，另一串看不到」／
     「一次最多合併 {MERGE_MAX} 則」
   - 底部右對齊 ghost 鈕「標記已處理」或「退回待處理」

**現在怎麼分群**：標頭與合併列用 `border-b`；清單項目**已經有卡片**（`rounded-lg border p-2.5`），
但用的是 `bg-card/50` ＋ `border-border/70` 的輕量卡，不是摘要工作台那套。
狀態靠邊框強弱：勾選 `border-signal`、選中 `border-signal-line`、皆 `bg-signal-wash`，不可勾 `opacity-45`。

---

## 2. 草稿回覆工作區（主區）

`components/draft/DraftReplyWorkspace.tsx`。骨架：來源卡橫跨全寬，其下 `xl:grid-cols-[280px_1fr]`

**空狀態**：圓形圖示 ＋「從左側收件匣點一則 Mention」＋
「系統會取回該討論串的完整對話，你可以再勾選其他 Space 當作 Reference Space 補充脈絡——被 @ 的問題，答案通常不在提問的那個 Space 裡。」

### A. 來源卡（全寬頂帶）`MentionSourceCard.tsx`

`{space_name}` ＋ `{sender_display} · {formatDateTime}` ＋ 狀態 badge 三選一：
「待處理」（signal 邊框/底）／「自選對話」／「✓ 已處理」（後兩者灰）

`manual` 時額外一行：「你從摘要工作台挑的對話，不是別人 @ 你，所以不在收件匣的待辦清單裡。送出回話後會歸到「已處理」。」

內文放在 `rounded-lg border bg-muted/40` 的凹陷框。

### B. 設定側欄（280px）`DraftSetupPanel.tsx`

順序刻意：模型/生成在上、資料來源在下。

- **Reference Space**：標題 ＋「已勾選 {n}」＋（有勾時）「清空」；
  說明「預設一個都不勾。勾選的 Space 近期訊息會一併送進脈絡。」；
  搜尋框 placeholder「搜尋 Space 名稱…」；欄位「每群抓取則數（1~1000）」；最後一列是供應商選擇
- **回覆設定**（預設展開，可收合；收合時右側顯示一行摘要）
  - **口氣**：第一項「跟隨預設（{serverDefault}）」＋副說明「不指定口氣，沿用你的偏好或系統預設。」；
    其餘每項＝`{label}` ＋ `{description}` ＋ signal 色「例：{example}」
  - **Persona**（右上 ghost 鈕「管理」）：「跟隨預設」／「不使用 Persona」／各個啟用的 Persona；
    選中後有 `PersonaNotice` 卡：來源行、「最後更新 {YYYY-MM-DD}」或「匯入於 {YYYY-MM-DD}」，
    公眾人物時 caution 色提示
  - **自訂提示**（右上「管理」）：下拉（「跟隨預設」／「不套用」／各 preset；**一則都沒有時整個下拉不畫**）
    ＋ textarea，placeholder「例如：不要太客套，直接說目前卡在哪裡；如果需要對方補資料，就列出需要的資訊。」
  - **Sepia 潤稿**：checkbox ＋「使用 Sepia 潤稿」（provenance 色 sparkles 圖示）＋
    「只調整〈建議回話〉的自然度與節奏，不會改動事實、數字或程式碼佐證。」；不可用時 caution 色原因
- **Space 清單**：label「一起當作參考的 Space」，空「查無符合的 Space」，固定 `h-64`
- **參考專案**（無專案則整塊不畫）：標題 ＋「已勾選 {n}」＋「清空」；
  「同一個專案可同時勾正式與 UAT，草稿會分開講兩邊的差異。」；
  勾了才出現「自己指定檢索關鍵字（選填）」，placeholder「例：sendPush retryCount」；
  提示二選一：「會用這 {n} 個關鍵字搜，取代系統自動抽的：{a、b}」／
  「留空就讓系統自己從問題裡抽。抽不準時填這裡，用逗號或空白分隔。」；
  每個專案下一排環境 chip（環境名 ＋ 等寬 `{branch}`）
- **產生鈕**（釘在欄底，捲動區外）：「產生 Draft Reply」／「重新產生 Draft Reply」／
  「{產生|重新產生} Draft Reply（合併 {n} 則）」；串流中換成 outline 的「停止串流」

### C. 產出區 `DraftOutputPane.tsx`

- 空狀態：「勾好 Reference Space 之後按「產生 Draft Reply」。草稿永遠只是草稿，一定要你看過、改過、確認後才會送出。」
- 還原提示條：「這是先前存下來的草稿」＋「產生於 {formatDateTime}」＋二選一
  「證據欄是產生當下記錄的完整內容。」／
  「產生當下的脈絡證據沒有保存，證據欄只看得到生成、回話設定與潤稿。要拿到完整證據請重新產生。」
- 潤稿退回條：「Sepia 潤稿未採用」＋ `{fallback_reason}` ＋「下面顯示的是未潤稿的版本，內容仍然可以直接送出。」
- **〈脈絡分析〉**（CompassIcon，signal 色）→ Markdown 放在 `rounded-xl border bg-card/60 p-4`
- **〈建議回話〉**：標題 ＋「（可直接編輯）」＋ 右側主鈕「送出回話」；
  textarea placeholder「建議回話會串流到這裡，你可以直接修改。」

**現在怎麼分群**：幾乎**全靠 border 規線 ＋ 間距**。側欄每一區是 `border-t px-3 py-2.5` 的水平帶、沒有卡片；
產出區兩個內容框有 `rounded-xl border`，但**沒有標頭帶**（標題浮在框外）。
**這是四個畫面裡改動量最大的一個。**

---

## 3. 證據欄（右欄，`w-rail`，≥1280 常駐）

`components/evidence/EvidenceColumn.tsx`；<1280 改為 `EvidenceDrawer.tsx`

**區塊**

1. **標頭**：「證據」＋ 串流中 `⟳`（aria-label「正在生成」）＋ 右側 caution 色「† 需要看一眼 {n} 項」
2. **空狀態**（依 origin）
   - draft：「產生 Draft Reply 之後，這裡會列出它建立在什麼之上——脈絡取了幾則、圖片讀進去幾張、程式碼命中哪些檔案、潤稿有沒有採用。」
   - summary：「產生 Summary 之後，這裡會列出它讀了哪些訊息、用了哪個模型。」
3. **證據列表**：`<dl>`，每列三欄 grid `[1fr_var(--container-metric)_1.75rem]`＝標籤／右對齊數字／單位。
   **所有列共用同一個右緣**——這是它最主要的識別特徵。
4. **底部小計**（非空且非串流）：左「證據 {n} 項」、右「降級 {n} 項」（>0 時 caution 色）

**四種狀態與線型語意**（規線＝語意，不是裝飾）

| 狀態 | 線型 | 數值欄 |
|---|---|---|
| `ok` | `border-line-evidence border-solid` | 正常數值 |
| `degraded` | `border-caution-line border-dashed`（斷掉的脈絡畫成斷線） | reason 前綴 `† ` |
| `missing` | `border-line-evidence border-dotted` | `—` |
| `pending` | `border-line-evidence border-solid` | 閃動的 signal 細豎條 |

`critical` 的列不可收合；其餘明細用 `▸ 明細` / `▾ 明細` 折疊。潤稿 ok 時 summary 前綴 `✓ `（verified 色）。

**列的文案**（`lib/evidence.ts`）：「脈絡」（summary 情境為「前後脈絡」／「討論串」／「討論串＋鄰近」，單位「則」）、
「回覆對象」（「則」；reason「送出後這 {n} 則會一起標記為已處理」）、
「附件」（「張」；「有 {n} 張沒有送進模型」、明細「略過」）、「參考 Space」（「個」）、
「參考專案」（`{project_name}　{environment_label}`，單位「檔」；空時「這個分支沒有找到相符的程式碼」，連結「檢查分支設定」）、
「生成」（供應商名）、「回話設定」（明細「口氣」／「Persona」（無則「未使用」）／「自訂提示」＝「已套用」）、
「潤稿」（「Sepia 已核對」／「Sepia 未採用」／串流中「Sepia 潤稿中」）、summary 情境的「來源」。
缺值說明：「這個版本的伺服器沒有回報脈絡形狀」／「這個版本的伺服器沒有回報圖片張數」；
還原草稿統一「這是從紀錄還原的草稿，產生當下的這項證據沒有保存」。

**抽屜**：`fixed top-(--topbar-h) right-0` 的 `w-rail` 面板，`border-l` ＋ `shadow-overlay`，
頂部只有一顆「關閉」。不自動開、不 inert 主區、無遮罩。

**歷史 Summary**（只在 summary 側掛在證據欄下方）：「歷史 Summary」＋右側「{n} 份」；
空「還沒有產生過 Summary。產生後會自動出現在這裡（僅你本人可見）。」；
每列 `{space_name}` ＋ `{formatDateTime} · {通用|技術細節|只要待辦} · {n} 則`

**現在怎麼分群**：**完全沒有卡片**。整欄靠 `border-b`（狀態化線型）＋ `px-gutter-tight` 的節奏。
⚠️ 把它包成卡片前要留意：**線型本身承載語意**（`--line-evidence` 受 WCAG 1.4.11 管，≥3:1），
不能被卡片的裝飾邊框吃掉。這是四個畫面裡語彙最獨特的一個。

---

## 4. 設定中心（全螢幕覆蓋層）

`components/settings/SettingsOverlay.tsx`，`role="dialog" aria-modal`

**骨架**：頂列（h-12，`border-b`）→ 左 nav（`w-inbox`，`border-r`）→ 右 main（`max-w-3xl` 置中）

- **頂列**：「設定」＋「這裡改的是「以後每次」的預設值」（sm 以上才顯示）＋右側「關閉」
- **分頁 nav**（順序＝最常改→最少改，每項＝粗體名稱 ＋ 灰色副標，active 時 `bg-signal-wash text-signal` 圓角塊，**不是卡片**）
  1. 回覆預設值 —「口氣、Persona、提示詞、潤稿」
  2. Persona —「匯入、更新、看淨化掉了什麼」
  3. 常用提示詞 —「存起來重複使用的提示」
  4. 參考專案 —「程式碼佐證的分支對應」
  5. Space —「釘選常用的、給私訊取名」
  6. 資料 —「用量與歷史 Summary」

  （診斷頁存在於路由型別但**刻意不在這個清單裡**，它排在登入 gate 之前。）

**各頁標題／副標**

- **回覆預設值**：頁內標題「我的預設值」。欄位同草稿側欄。
  頁尾「這裡設定的是**以後每次**的預設值。只想改這一次的話，在草稿工作區的回覆設定改就好。」
  ＋主鈕「儲存為我的預設」，成功 toast「已把目前的回覆設定存成預設」
- **Persona**：「Persona 是「借用某個人的思考框架與表達習慣來寫回覆」，不是扮演那個人。匯入時會固定版本（記下 commit），遠端之後改了也不會影響已產生的回話。」
  子區塊「匯入」（Repository／網址 切換，placeholder `fxp/persona-distill-skills`、`luozhenyu`、
  `https://raw.githubusercontent.com/...`、「留空則用來源檔案裡的名稱」）；
  結果條「匯入失敗」／「匯入成功，但請確認一下」；空「還沒有匯入任何 Persona。」；每列有「停用」／「啟用」
- **常用提示詞**：「存起來重複使用的回覆要求。套用之後仍然可以在草稿頁微調——送出時以輸入框裡的內容為準。」
  表單標題「新增」／「編輯」，placeholder「我的工程師回覆」、「平常回工程團隊使用」、
  「回覆不要太正式。直接告訴對方目前問題在哪，如果需要他補資料，就明確列出需要哪些資料。」；
  鈕「儲存」／「更新」；空「還沒有儲存任何提示詞。」
- **參考專案**：「登錄這台機器上的程式碼資料夾，Draft Reply 就能引用實際程式碼回答問題。**每個環境對應哪個分支要在這裡講清楚**——拿 UAT 的程式碼回答正式環境的問題，會產生看起來有憑有據、實際上錯的答案。」
  另有「（預設）」標記與「，是不是 {建議}？」的 did-you-mean 提示
- **Space**：兩節。「釘選的 Space」—「釘選的 Space 會排在清單最前面。你加入了 {n} 個 Space，常用的其實只有幾個。」；
  載入中「正在載入 Space…」；空「還沒有釘選任何 Space。在下面搜尋並按「釘選」。」。
  「全部 Space」— 搜尋 placeholder「搜尋 {n} 個 Space 名稱…」；空「查無符合的 Space。」；列尾鈕「釘選」／「取消釘選」
- **資料**：兩節。「Token 用量」—「以每天、每個模型分開計。這裡選的天數會同步套用到右欄的用量面板。」；
  「統計範圍」＋「{n} 天」切換鈕組＋右側「合計 {total}」；空「這段期間沒有用量紀錄。」。
  「歷史 Summary」— 狀態行三選一：「正在載入…」／「還沒有產生過 Summary。」／「目前有 {n} 份。」

**現在怎麼分群**：頂列與 nav 用規線；頁內用 `space-y-6`/`space-y-8` 間距 ＋ `<h2>` 分節。
**只有 Persona 與常用提示詞兩頁的表單有卡片**（`rounded border p-3` 輕量素框，沒有標頭帶、沒有 `bg-surface`）。

⚠️ **既有不一致**：頁內標題字級兩派——`回覆預設值`/`Persona`/`常用提示詞` 用 `text-sm font-semibold`，
`參考專案`/`Space`/`資料` 用 `text-md font-semibold`。設計稿要統一。
