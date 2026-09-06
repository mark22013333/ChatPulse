# Draft Reply 脈絡取得：架構檢討與設計建議

> 產出日期：2026-09-06
> 範圍：`dashboard/api/server.py` draft_stream、`core/chat_client.py`、`core/attachments.py`、`core/prompts.py`
> 立場：本文刻意採批判角度。作者未參與原方案設計。
> **本文未修改任何檔案**（設計討論）。

---

## 第一段：對初步方案的批判

### C-1（最嚴重）｜根因診斷指到了錯的那一行，`else` 分支近乎死碼

你的描述是：

> `dashboard/api/server.py` 的 draft_stream 裡有一行 `else: thread_msgs = [mention_msg]`——沒有 thread_name 時脈絡只剩那一則。

實際證據不支持「沒有 thread_name」這個前提：

- `core/mentions.py:78`：採集器寫入 mention 時取 `(message.get("thread") or {}).get("name")`。
- `dashboard/api/server.py:770`：`create_draft_target()`（私訊走的就是這條）同樣取 `(target.get("thread") or {}).get("name")`。
- Google Chat 的 `Message` 資源**一定**帶 `thread.name`——即使是 UNTHREADED 的私訊，每則訊息也各自屬於一個只有自己的 thread（這正是你實測「最近 10 則分屬 6 個 thread」的原因）。

所以私訊走的是 `if thread_name:` 那一支，`list_thread_messages()` 回 1 則，不是走 `else`。`server.py:1409-1410` 的 `else` 分支只有在 Google 破例不回 thread 時才會執行——實務上幾乎不會。

**這為什麼重要**：不是為了糾正措辭。而是

1. 如果有人照這個診斷去「修那一行 else」，改完會**完全沒有效果**，而且很難察覺（草稿還是只有 1 則，但看起來已經修了）。
2. 真正的判斷點不是「thread_name 在不在」，而是「**這個 Space 的 threading 語意是什麼**」。這兩者導向不同的架構（見 C-2）。

你的方案裡的第二個條件「thread 只有 1 則 → 補時間窗」剛好會蓋到這個情況，所以**方案本身活得下來**；但它是靠症狀觸發，不是靠成因觸發，代價寫在 C-2。

---

### C-2｜用「thread 訊息數 > 1」當判準，是拿症狀當成因；正確的判準已經在快取裡了

`thread_msgs 長度 == 1` 這個訊號同時涵蓋**兩種語意完全相反**的情況：

| 情況 | 語意 | 周圍的扁平訊息是什麼 |
|---|---|---|
| A. 私訊／UNTHREADED 空間 | thread 不是對話單位，**扁平序列才是對話** | 就是同一段對話的前文 ✅ |
| B. 群組 THREADED 空間，有人 @ 你但還沒人回 | thread 是對話單位，這串只是還沒人接話 | **別的討論串的內容，是不同話題** ⚠️ |

你的方案對 A 和 B 套用同一條規則（「補該 space 最近 N 則」）。在 A 是對的，在 B 是把不相干的話題灌進「該討論串的完整對話」這個標題底下（見 C-3）。你自己在疑慮 2 提到了這件事，但把它當成「要不要做」的開關題；我認為它其實是「**A 跟 B 根本不該用同一條路徑**」的架構題。

而且成因訊號**不需要多打一次 API**：

- `server.py:271` 已經把 `spaceType` 存進 spaces 快取的 `type` 欄位；`server.py:332`、`server.py:199` 都已經在用 `== "DIRECT_MESSAGE"` 做判斷。
- `space_display_name()`（`server.py:299`）本來就會走一次這份快取，draft_stream 第 1459 行已經在呼叫它。

也就是說：**「這是不是私訊」在 draft_stream 裡是零成本可得的資訊**，而你選擇改用「先撈 thread、看回幾則」這個要多一次 Google API 往返、而且會把 B 誤判成 A 的間接訊號。

> [待實測] Google Chat 的 `Space` 另有 `spaceThreadingState` 欄位（語意上比 `spaceType` 精準：群組也可能是 GROUPED/UNTHREADED，那種群組的 thread 同樣不成立）。本 repo 目前完全沒用到它，`list_spaces()` 回傳裡有沒有這個欄位我**沒有實測驗證**。建議把它當成之後的精修，不要當成第一版的依賴——`spaceType == "DIRECT_MESSAGE"` 已經覆蓋你實測到的問題，且已被本專案驗證可用。

---

### C-3（你的方案漏掉的最大一項）｜只改「撈什麼」不改 prompt，等於對模型說謊

`core/prompts.py:218-219` 的區塊標題是寫死的：

```
【該討論串的完整對話】
{thread_text}
```

而 `draft_reply_prompt()` 的簽章只有一個 `thread_text: str`。你的方案說「把被 @ 的那則標示出來讓模型知道要回哪一則」，但沒有處理標題本身。後果分兩層：

1. **語意錯誤**：把「該 space 最近 20 則、分屬 6 個 thread」塞進「該討論串的完整對話」，模型會相信這些訊息是同一串的連續發言。在情況 B（群組）它會把別的討論串的結論，當成這一串的既定事實寫進〈脈絡分析〉的「關鍵決策」。這正是 `_BASE_RULES` 與「只寫你在上面資料中真的看得到的事實」擋不住的錯——資料確實看得到，只是歸屬錯了。
2. **「完整」二字是假承諾**：目前 1 則的情況下，prompt 寫著「完整對話」而底下只有一行，而且那一行跟上方【被 @ 的訊息】**完全重複**（`thread_text = format_conversation(...) or mention_text`，`server.py:1420`）。模型收到的訊號是「這段對話就只有這麼多，沒有更多脈絡了」——它不會說「我脈絡不足」，它會直接編。

**任何取脈絡的改動，都必須連帶改 `draft_reply_prompt()` 的區塊結構**，這不是加分項。至少要能表達三件事：這段脈絡的**來源**（同一串／同一聊天室的近期扁平訊息）、**邊界**（涵蓋哪段時間、幾則）、**錨點**（要回的是哪一則）。

---

### C-4｜「最近 N 則」對 Draft Reply 是錯的框架（你的疑慮 5 是對的，而且比你想的更嚴重）

你已經懷疑這點。補三個你沒列出的理由：

1. **被 @ 的那則不一定是最新的。** Mention 收件匣是佇列，使用者可能隔幾小時才處理。忙碌的群組在這段時間會再進 30 則。「最近 20 則」在極端情況下**會把錨點訊息本身排除在外**，於是脈絡窗跟要回的問題完全不重疊——而且不會報錯，只會產出一份答非所問的草稿。
2. **錨點之後的訊息同樣重要，而且方向不同。** 如果同事已經自己回答了，或補了一句「不用了我查到了」，草稿必須知道。這代表窗口是**雙向**的：錨點前要「補背景」、錨點後要「避免重複回答已解決的事」。「最近 N 則」只有在錨點正好是最後一則時才等價於雙向窗。
3. **可重現性。** 草稿會存進 DB（`repo.create_draft`）。「最近 N 則」讓同一個 mention 重新產生草稿時輸入不同，使用者會看到兩份不一致的草稿而找不出原因。以錨點為中心的窗在同一個錨點下是穩定的。

---

### C-5｜圖片預算的真正風險不是「擠掉文字」，是「圖片取樣範圍被靜默放大」

你的疑慮 3 問的是「圖片會不會擠掉文字」。實際機制不是這樣：

- `providers` 的圖片與文字是分開傳的（`ai.stream_text(prompt, images=images)`），圖片預算 8000 tokens 是獨立的上限，不會「擠掉」prompt 文字。兩者是相加，不是競爭。
- 真正的問題在 `server.py:1447-1453`：`attachments.collect()` 吃的訊息清單**就是 `thread_msgs`**。你把 `thread_msgs` 從「同一串」換成「該 space 最近 N 則」，就同時把圖片的取樣母體換掉了，而這個副作用在 diff 上看不出來——那一行完全沒改。

後果：在群組情況 B，別的討論串的截圖會進入候選，`find_candidates()` 依「越新越優先」排序（`attachments.py:90`），於是**一張跟提問無關但比較新的圖，會排在被 @ 那則的圖之後、但排在同串較舊的圖之前**，把 8 張／8000 tokens 的額度吃掉。`priority_message_names` 只保障錨點那一則排第一，保障不了第 2~8 張的相關性。

附帶一個既有缺陷：draft 路徑呼叫 `collect()` 時**沒有傳 `scan_recent`**，吃預設 `IMAGE_SCAN_RECENT_MESSAGES = 30`（`config.py:111`）。目前 thread 很短所以無所謂；脈絡放大之後這個 30 就開始有意義了，而它是為「摘要 500 則」設計的數字，不是為草稿設計的。

**結論：文字脈絡與圖片脈絡必須解耦成兩份清單**，不能繼續共用 `thread_msgs` 這一個變數。

---

### C-6｜成本分析的方向可能是反的

你的疑慮 4 假設「脈絡變大 = 成本上升，且 gemini 免費層更敏感」。實際上：

- **gemini 免費層的配額單位是「每天 20 次請求」**，不是 token 數。在這個配額模型下，prompt 從 1 則長到 20 則，**邊際成本是零**。真正會消耗配額的是「使用者因為草稿沒脈絡而重新產生一次」——脈絡不足反而更貴。
- **claude_cli 吃訂閱**，成本也不是按 token 線性計價的使用者可感金額，而是 rate limit。`config.py:52-57` 已經為了避免 19,085 tokens 的快取寫入而關掉全部工具——相較之下，多 20 則對話文字（粗估 1~2k tokens）在同一個量級之下。

也就是說：**成本不是這個決策的主要約束**，把它列為主要疑慮會讓你在「窗口大小」上做過度保守的選擇。真正的約束是**訊噪比**（灌進不相關內容會讓草稿變差）和**延遲**（多一次 Google API 往返）。這兩個都不會因為「把 N 從 20 調到 10」而顯著改善，卻會因為「窗口的形狀選錯」而顯著惡化。

---

### C-7｜`limit` 參數語意超載

`DraftRequest.limit`（`server.py:1278`）目前**只**用在 Reference Space（`server.py:1425`），前端標籤是「每群抓取則數（1~1000）」（`DraftReplyWorkspace.tsx:196`）。若你讓同一個 `limit` 也控制主 Space 的時間窗，會出現：

- 一個數字控制兩件性質不同的事（跨群補充脈絡 vs 主對話回溯深度），使用者調整其一必然誤調另一。
- 前端標籤變成錯的，而且是**靜默**錯——UI 上看不出來。

主 Space 的窗口需要自己的參數，且預設值不該跟 Reference Space 綁在一起。

---

### C-8｜兩個既有的鄰近缺陷，一起處理成本很低，分開處理會變成第二次改動

1. **thread 路徑沒有上限保護。** `server.py:1407` 傳 `limit=cfg.LIMIT_MAX`（= 1000）。一個真的很長的討論串會把 1000 則灌進 prompt，而 Reference Space 那邊有 `limit` 卡著。你正在為私訊「加脈絡」，同一次也該為群組長串「設上限」——否則系統在兩端都失控，只是方向相反。
2. **`create_draft_target()` 的錨點選擇可能挑錯訊息。** `server.py:755-758` 取「最後一則不是自己發的訊息」。私訊裡對方常把一個問題拆成三則發（「你好」「想問一下 X」「方便的話今天回我」），錨點會落在最後那則客套話上，而真正的問題在前兩則。這在目前只撈 1 則的架構下是致命的；改成錨點前後窗之後會被脈絡救回來，但〈被 @ 的訊息〉區塊仍會顯示錯的那一則，模型會照著回。
3. **`thread_text` 與 `mention_text` 重複。** `server.py:1420` 的 `or mention_text` fallback，在 1 則的情況下讓同一句話在 prompt 出現兩次。窗口修好之後這個 fallback 應該一併移除或改寫，否則會變成「錨點那則出現兩次」。

---

### C-9｜與 ADR-0003 的張力：這是不是在偷偷做 RAG？

ADR-0003 明確拒絕跨群語意檢索，理由是「你已經知道答案在哪個群」。你要加的東西**不違反**它——你補的是**同一個 Space 內**的時間相鄰脈絡，不是跨群語意檢索，也不需要索引。這點值得在設計文件裡寫明，否則下一個人會拿 ADR-0003 來反對這個改動，或反過來把它當成「RAG 已經開了個口」的先例。

但有一條界線要守住：**只要開始做「挑哪幾則比較相關」的評分，就是 RAG 的第一步。** 建議這一版只用結構性規則（時間相鄰、同一 thread、同一發話者），不引入任何相關性評分。

---

## 第二段：建議方案

### 設計原則（先講框架，再講程式碼）

使用者的原話是「應該要能有連貫性」。我認為**連貫性不是「兩條路徑都用同一個撈法」**——摘要要的是「這個聊天室最近發生什麼」（無錨點、涵蓋導向），草稿要的是「要回的這句話，前後脈絡是什麼」（有錨點、理解導向）。硬把兩者統一成同一個撈法，會讓其中一個變差。

真正該統一的是**三件事**：

1. **脈絡的邊界必須由 Space 的結構語意決定**（thread 是不是對話單位），而不是由「撈回來剛好幾則」決定。
2. **脈絡必須帶著自己的元資料**（來源、範圍、錨點在哪）一路送到 prompt，不能退化成一個沒有標籤的字串。
3. **文字脈絡與圖片脈絡是兩個不同的範圍**，不能共用同一個變數。

現狀違反這三條；你的初步方案修了第 1 條的一半，沒碰第 2、3 條。

---

### 2.1 新增 `core/draft_context.py`

放在 `core/` 與 `attachments.py`、`code_search.py` 同層——它跟它們一樣是「取脈絡的政策」，不是 API 封裝（那是 `chat_client.py` 的職責）。`server.py` 的 `draft_stream` 已經 145 行，繼續往裡塞會變成第二個 God function。

```python
# core/draft_context.py

@dataclass
class ContextBlock:
    kind: str            # "thread" | "dm_window" | "cross_thread"
    label: str           # 給 prompt 的中文標題
    messages: List[dict]
    text: str            # format_conversation 的結果，錨點那行帶 ▶ 前綴

@dataclass
class DraftContext:
    anchor_messages: List[dict]   # 錨點「那一串連發」，見 2.4
    blocks: List[ContextBlock]
    image_messages: List[dict]    # 專供 attachments.collect，見 2.5
    mode: str                     # "thread" | "dm_window" | "thread_thin"
    coverage: str                 # "full" | "partial"（錨點超出可取範圍）
    message_count: int
    time_range: Tuple[str, str]

def build(client, *, space_id, space_type, anchor_msg, resolve) -> DraftContext:
    ...
```

### 2.2 判斷條件（具體、可勾選）

判準用 `spaceType`，**不是** thread 長度。取得方式零成本：`server.py:271` 的 spaces 快取已經有 `type` 欄位，`draft_stream:1459` 本來就會走一次那份快取（`space_display_name`）。新增一個 `space_type_of(viewer_id, space_id)` 輔助函式即可（與 `server.py:332` 既有寫法一致）。

```
if space_type == "DIRECT_MESSAGE":
    mode = "dm_window"
    #   不呼叫 list_thread_messages —— 私訊的 thread 恆為 1 則，
    #   那一次 API 往返純粹是浪費（現狀每產一次私訊草稿就浪費一次）
    blocks = [ 錨點窗（2.3） ]

else:                               # SPACE / GROUP_CHAT
    thread_msgs = client.list_thread_messages(space_id, thread_name,
                                              limit=cfg.DRAFT_THREAD_LIMIT)   # ← 不再是 LIMIT_MAX
    if len(thread_msgs) >= 2:
        mode = "thread"             # ★ 維持現狀，一個字都不改
        blocks = [ ContextBlock(kind="thread", messages=thread_msgs) ]
    else:
        mode = "thread_thin"        # 有人 @ 你但還沒人回
        blocks = [ ContextBlock(kind="thread", messages=thread_msgs),
                   ContextBlock(kind="cross_thread", messages=小窗（2.3，K=8，僅 before）) ]
```

**為什麼群組 thread ≥ 2 完全不動**（直接回答你的疑慮 2）：群組已經有一個給使用者控制的補脈絡機制——Reference Space（ADR-0003）。群組缺脈絡時使用者**知道**該勾哪個群，那個判斷本來就不該給系統做。私訊的問題性質完全不同：它連「同一段對話的前一句」都拿不到，那不是補充脈絡，那是**預設就該有的基本盤**。這條界線把「該補」與「不該補」分得很乾淨，而「thread 長度 > 1」這個判準分不出來。

**`cross_thread` 為什麼還是要給**（但只給小的、標好的）：`thread_thin` 的典型形態是「PM 丟一句 @你 這個怎麼辦」，這種訊息不可能自足。給 8 則背景讓模型知道「今天這個群在吵什麼」有價值。但它必須用**獨立區塊 + 警語**（2.6），且建議做成 config 開關 `DRAFT_CROSS_THREAD_ENABLED`，實測若發現群組草稿變差可以一鍵退回。

---

### 2.3 錨點窗：怎麼取「前後各 K 則」

**取法（只用既有、已驗證的方法，不引入新的 filter 語法風險）**

```python
w = client.fetch_recent_messages(space_id, limit=cfg.DRAFT_WINDOW_FETCH)   # 60，已驗證的方法
idx = next((i for i, m in enumerate(w) if m.get("name") == anchor_name), None)

if idx is not None:
    before = w[max(0, idx - K_BEFORE) : idx]
    after  = w[idx + 1 : idx + 1 + K_AFTER]
    coverage = "full"
else:
    # 錨點比最近 60 則還舊（收件匣積壓、或很久以前的私訊）
    # 用已驗證的 createTime > 語法時間錨定；list_messages_since 需加一個 max_messages 上限
    near = client.list_messages_since(space_id,
                                      anchor_time - timedelta(hours=cfg.DRAFT_WINDOW_HOURS),
                                      max_messages=cfg.DRAFT_WINDOW_FETCH)
    ... 同樣切片 ...
    coverage = "full" if 找到錨點 else "partial"
```

第二層 fallback 只用 `createTime >`——`chat_client.py:313` 的 docstring 已實測 `>=` 回 400，`>` 可用。**我沒有實測 `createTime <` 是否可用**，所以整個設計不依賴它。

**回答你的疑慮 1 與疑慮 5：窗口大小與形狀**

不要跟摘要共用數字。摘要的 N 是「要摘多少東西」（產出涵蓋範圍），草稿的 K 是「要理解到多深」（輸入理解深度）；使用者把摘要從 50 調到 20，是因為 50 則的摘要讀起來太長，這跟草稿該看幾則毫無關係。共用會讓「調整摘要長度」意外劣化草稿品質，而且沒有任何跡象。

```python
# core/config.py（與既有 LIMIT_* / IMAGE_* 同一區）
DRAFT_WINDOW_FETCH   = 60   # 一次撈多少來找錨點
DRAFT_CTX_BEFORE     = 15   # 錨點之前保留幾則
DRAFT_CTX_AFTER      = 10   # 錨點之後保留幾則
DRAFT_CTX_MIN_BEFORE = 6    # 時間上界過濾後至少保留幾則（見下）
DRAFT_WINDOW_HOURS   = 48   # 時間上界
DRAFT_THREAD_LIMIT   = 60   # 取代 draft 路徑寫死的 LIMIT_MAX=1000
DRAFT_CROSS_BEFORE   = 8    # thread_thin 的跨串小窗
```

- **before 15**：你實測「私訊最近 10 則分屬 6 個 thread」＝ 平均一個話題約 1.7 則，15 則約涵蓋 2~3 輪話題交替，足以回答「他在問什麼、上次講到哪」。
- **after 10**：目的不是理解背景，是偵測「已經有人回答了／對方自己說不用了」。10 則綽綽有餘。**這是「最近 N 則」框架完全給不了的東西**——它只在錨點正好是最後一則時才等價。
- **時間上界 48h，但保底 6 則**（這是關鍵細節）：count 窗與 time 窗**取交集，但保證錨點前至少留 6 則**。
  - 只用 count 窗 → 冷清的私訊會撈到三個月前的閒聊當「脈絡」（你疑慮 5 擔心的正是這個）。
  - 只用 time 窗 → 冷清的私訊會一則都不剩，等於沒修。
  - 交集 + 保底：熱絡對話被 48h 截斷（正確，48h 前的不是脈絡）；冷清對話至少拿到最近 6 則（正確，那就是「上次聊到哪」）。

---

### 2.4 錨點應該是「一串連發」而不是一則（順手修 C-8.2）

`server.py:755-758` 挑「最後一則不是自己發的訊息」當錨點。私訊常見形態是一個問題拆三則發，錨點會落在最後那句客套話上，於是【被 @ 的訊息】顯示的是「方便的話今天回我」，模型就照這句回。

在 `draft_context.build()` 裡收攏一個 **run**（純結構規則，不做相關性評分，守住 C-9 的界線）：

```python
anchor_run = [anchor]
i = idx - 1
while i >= 0 and same_sender(w[i], anchor) and gap(w[i], w[i+1]) < timedelta(minutes=5):
    anchor_run.insert(0, w[i]); i -= 1
```

prompt 的【被 @ 的訊息】區塊改成印整個 run。`before` 窗從 run 的最前面往前算，避免重複。

---

### 2.5 圖片：解耦成獨立清單（回答疑慮 3）

`server.py:1447-1453` 目前把 `thread_msgs` 直接餵給 `attachments.collect()`。文字脈絡一放大，圖片取樣母體就跟著放大——而那一行 diff 上看不出任何改動，是最容易漏掉的副作用。

```python
# core/draft_context.py 產出
image_messages = anchor_run + thread_msgs + before[-6:]     # 不含 after、不含 cross_thread

# server.py
images, skipped = attachments.collect(
    client.download_attachment,
    ctx.image_messages,
    space_id=mention["space_id"],
    budget_tokens=cfg.IMAGE_BUDGET_TOKENS_DRAFT,
    priority_message_names=[m["name"] for m in ctx.anchor_run],   # 整串連發都優先
    scan_recent=len(ctx.image_messages),   # ← 明示，不再吃為「摘要 500 則」設計的預設 30
)
```

三個排除各有理由：

- **排除 `cross_thread`**：別的討論串的截圖幾乎必然不相關，而 `attachments.py:90` 依「越新越優先」排序，一張較新的無關圖會排在同串較舊的相關圖前面，把 8 張／8000 tokens 吃掉。`priority_message_names` 只保障第 1 張，保障不了第 2~8 張。
- **排除 `after`**：錨點之後的圖多半是**別人回答時貼的**。給模型看等於誘導它抄別人的答案（而使用者要的是自己的回話）。這些訊息的**文字**仍在脈絡裡（讓模型知道已被回答），只是不送圖。
- **`before` 只取 6 則**：貼圖脈絡的有效距離比文字短得多。

順帶：`budget_tokens=8000` 與 `max_count=8` 不需要調。C-5 已說明圖片與文字是分開傳的，不存在「圖片擠掉文字」——真正的風險是取樣範圍，已由上面解決。

---

### 2.6 Prompt 改動（沒有這一段，前面全部白做）

`core/prompts.py:174` 的 `draft_reply_prompt()`：

**簽章**：`thread_text: str` → `context_blocks: List[Dict]`（每個含 `kind / label / count / time_range / text`）。`reference_blocks` 與 `code_blocks` 保持不變。

**輸出結構**：

```
【被 @ 的訊息】
聊天室：{space_name}（{space_type_label}）
提問者：{mention_sender}
內容：{anchor_run 全文，多則就分行}

【對話脈絡】
〔涵蓋範圍〕共 {N} 則，{起} ~ {迄}。要回覆的那一則在下方以 ▶ 標記。
這是系統能取到的全部；超出這個範圍的內容你看不到。資訊不足以回答時，
請在回話中明確問回去缺什麼，**不要推測、不要填補**。

--- {block.label} ---
{block.text}
```

三種 `kind` 的 `label` 與說明句：

| kind | label | 說明句 |
|---|---|---|
| `thread` | 該討論串的完整對話（共 N 則） | （不需額外說明，維持現狀語意） |
| `dm_window` | 這個私訊在該則前後的連續對話（前 N1 則、後 N2 則） | 「私訊沒有討論串結構，**這個時間序列本身就是對話**。」 |
| `cross_thread` | ⚠️ 同一聊天室其他討論串的近期訊息（共 N 則） | 「這些訊息**不屬於**你要回覆的那一串，只是時間相近的其他討論。僅供理解背景，**不可**當成本串已經談定的結論，也不要在回話中引用。」 |

四個要點：

1. **刪掉「完整對話」這個假承諾**，換成可稽核的「共 N 則、起迄時間」。這正面修掉 C-3——模型現在知道自己看到的邊界在哪，才可能誠實地說「資訊不足」。
2. **▶ 標記**：`format_conversation()` 不動（它被摘要共用），在 `draft_context` 裡對錨點那幾行加前綴即可。
3. **`cross_thread` 的警語必須在區塊「之前」**，不是統一放在 `_BASE_RULES` 最後——模型對就近的指令服從度較高。
4. `【輸出格式】`〈脈絡分析〉裡建議加一條 `- **脈絡涵蓋**：（你看到的是哪個範圍、有沒有明顯缺口）`，讓「脈絡不足」變成一個模型必須正面回答的欄位，而不是它可以沉默略過的事。

---

### 2.7 `draft_stream` 的逐行改動清單

檔案：`dashboard/api/server.py`

| 行 | 現況 | 改成 |
|---|---|---|
| 1404-1411 | `if thread_name: … else: thread_msgs=[mention_msg]` | `ctx = draft_context.build(client, space_id=…, space_type=space_type_of(viewer_id, space_id), anchor_msg=mention_msg, resolve=resolve)` |
| 1420 | `thread_text = format_conversation(...) or mention_text` | 刪除（`or mention_text` 的重複問題一併消失，見 C-8.3） |
| 1444-1453 | `attachments.collect(..., thread_msgs, ...)` | 改吃 `ctx.image_messages`，補 `scan_recent`、`priority_message_names` 用整個 run |
| 1460 | `"thread_message_count": len(thread_msgs)` | `"context": {"mode": ctx.mode, "message_count": …, "time_range": …, "coverage": ctx.coverage}` |
| 1479 | `collect_code_context(..., thread_text)` | 傳 `ctx.context_text_for_search`（供 `code_search.extract_search_terms` 抽詞；語意不變但來源改名） |
| 1502-1509 | `thread_text=thread_text` | `context_blocks=ctx.blocks` |
| 1407 | `limit=cfg.LIMIT_MAX`（1000） | `limit=cfg.DRAFT_THREAD_LIMIT`（60）——修 C-8.1 |

`DraftRequest`（1274）**不新增欄位**。理由見 C-7：`limit` 已經是 Reference Space 專用，再塞一個主窗參數會讓前端「每群抓取則數」的標籤變成靜默錯誤。第一版全部走 config 常數；等實際用過、知道對的預設值是多少，再決定要不要開給前端。多一個旋鈕就多一個要解釋、要驗證、會被調錯的東西。

**前端**（`DraftReplyWorkspace.tsx`）：把 meta 的 `context` 顯示出來——「本次脈絡：私訊前後 25 則，09-04 10:12 ~ 09-06 14:33」。這是使用者判斷這份草稿可不可信的**唯一**依據，現在完全沒有。

---

### 2.8 如果只想先止血：Phase 0

不想一次做完的話，最小且不會走回頭路的版本是——但**這兩件事不能拆開做**：

1. `draft_stream` 在取完 `thread_msgs` 之後加判斷：`spaceType == "DIRECT_MESSAGE"` 時改用錨點窗。
2. **同時**把 `draft_reply_prompt` 的區塊標題從「該討論串的完整對話」改成帶來源與範圍的版本。

只做 1 不做 2，等於把 6 個不同 thread 的內容塞進標著「同一討論串」的區塊——這比現在只有 1 則更危險，因為錯誤會從「明顯的資訊不足」變成「看起來很有脈絡的錯誤歸因」。

---

### 2.9 驗證（這個 repo 目前沒有單元測試網）

`tests/` 底下只有 `e2e/`，且沒有任何檔案引用 `draft_reply_prompt` 或 `list_thread_messages`。這代表改 prompt 簽章不會弄壞既有測試——但也代表**沒有任何東西會攔住你改錯**。建議至少補：

- `draft_context.build()` 的純函式測試：餵一份假的訊息列表（不打 API），驗四種情況各自的 `mode` / `blocks` / `image_messages` 內容：DM 錨點在中間、DM 錨點在最舊（`coverage="partial"`）、群組長 thread（要驗證**輸出與改動前完全一致**）、群組薄 thread。
- 迴歸的正對照：拿你已經實測過的那個私訊，改前改後各跑一次，比對 meta 的 `message_count`（應從 1 變成 20 上下）與 `image_count`（應從 1 變成 4 上下）。**這是唯一能證明真的修好的證據**，不要只看草稿讀起來變好了。

---

## 第三段：取捨說明（我的方案在什麼情境下比較差）

### T-1｜48h 時間上界會切掉「隔很久重問同一件事」的脈絡

最痛的一種：對方三週前在私訊問過一次、當時討論到一半停了，今天丟一句「上次那個後來怎樣？」。48h 上界會把三週前那段完全切掉，草稿看到的只有「上次那個」四個字。

「最近 N 則」在**冷清的**私訊裡反而可能涵蓋到（因為三週內只有 10 則訊息）。

- 緩解：`DRAFT_CTX_MIN_BEFORE = 6` 的保底會讓冷清私訊仍拿到跨越數週的最近 6 則，能救回一部分。
- 但**熱絡的**私訊（48h 內就超過 15 則）會被完整切在 48h，這種情況我的方案確實比純 count 窗差。
- 我仍選擇加上界，因為反例更常見也更難察覺：沒有上界時，「一個月前的閒聊」會以完全相同的格式混在脈絡裡，模型分不出新舊，會拿舊結論當現況。錯誤的脈絡比缺少的脈絡更貴——缺少時模型至少可能說「資訊不足」。
- 如果實測發現 T-1 常發生：把 `DRAFT_WINDOW_HOURS` 調到 168（一週），不要拿掉。

### T-2｜`spaceType == "DIRECT_MESSAGE"` 這個判準會漏掉「不用討論串的群組」

Google Chat 的群組可以建成不分串的形態（所有訊息扁平）。這種群組的 `spaceType` 不是 `DIRECT_MESSAGE`，但它的 thread 語意跟私訊一樣——每則各自成串。我的方案會把它判成 `thread_thin`，於是它拿到的是**帶警語的 8 則 `cross_thread` 小窗**，而不是它應得的「這就是對話本身」的 25 則 `dm_window`。

結果：警語會讓模型對真正相關的脈絡過度保守（「不可當成本串已談定的結論」——但那其實就是同一段對話）。

- 這是我的方案**明確比你的方案差**的一格：你的「thread 只有 1 則就補窗」對這種群組是對的。
- 緩解路徑：`spaceThreadingState` 是正確的判準，但我**沒有實測**它在本專案的 `list_spaces()` 回傳中是否存在。實測方式很便宜——挑一個已知不分串的群組，`client.get_space(space_id)` 看回傳有沒有那個欄位。有的話把 2.2 的判準換成 `spaceThreadingState in ("UNTHREADED_MESSAGES", "GROUPED_MESSAGES")`，`spaceType` 只當 fallback，T-2 就消失。
- 若你的工作群組實際上都是分串的（台灣職場 Google Chat 群組多半是），這一格的實際損失接近零。**先確認這件事再決定要不要為它加複雜度。**

### T-3｜`after` 窗可能讓模型改寫成「這題已經有人回了」而不是回話

我加 `after` 是為了避免重複回答已解決的事。但模型看到「同事已經回答」時，很可能把〈建議回話〉寫成「看起來 XXX 已經回覆了，我補充一下…」——而使用者要的是一段可以直接送出的話。

- 緩解：`draft_reply_prompt` 的〈建議回話〉要求裡加一句「若脈絡顯示這個問題已被他人回答，仍要寫出你自己的回話，並在〈脈絡分析〉的『未解問題』註明已被回答」。
- 這是新增的失敗模式，現狀沒有。若實測發現常發生，把 `DRAFT_CTX_AFTER` 設 0 就能退回——這是最容易回退的一項。

### T-4｜thread 門檻用「則數 ≥ 2」仍然粗糙

群組裡「PM @ 你 → 有人回一個 👍」的 thread 有 2 則，會被判成 `mode="thread"`，於是一則脈絡都不補。這種 thread 實質上跟只有 1 則沒差別。

- 我接受這個粗糙，因為替代方案（判斷「有意義的回覆」）需要內容評分，那會踩到 C-9 的界線。
- 若要調，只調數字（門檻設 3），不要引入內容判斷。

### T-5｜延遲增加：群組薄串路徑從 2 次 API 呼叫變 3 次

| 路徑 | 現狀 | 建議方案 |
|---|---|---|
| 私訊 | `get_message` + `list_thread_messages`（**這次呼叫必然只回 1 則，純浪費**） = 2 | `get_message` + `fetch_recent_messages` = **2**（不變，且第 2 次有用） |
| 群組長串 | `get_message` + `list_thread_messages` = 2 | 同左 = **2**（不變） |
| 群組薄串 | 2 | + `fetch_recent_messages` = **3**（劣化） |

只有第三種劣化，而它是新增功能的必要成本。若在意，`cross_thread` 路徑的 `fetch_recent_messages` 可以只撈 25 則（它只用 8 則），比主窗的 60 便宜。

### T-6｜diff 比你的方案大，而且沒有單元測試網

你的方案是在 `draft_stream` 裡加一個 `if`；我的方案動了 4 個檔案（新增 `core/draft_context.py`、改 `server.py` 7 處、改 `prompts.py` 簽章、改 `config.py` 常數）＋前端一處。而 `tests/` 只有 e2e，**沒有任何測試會攔住改錯**（見 2.9）。

- 這是真實的風險，不是形式上的。若你現在的優先級是「今天就要能用」，走 2.8 的 Phase 0（但那兩件事不能拆）。
- 但 Phase 0 的方向與完整方案一致、不會走回頭路——這是我把它列出來的原因。

### T-7｜`cross_thread` 警語可能無效

我假設「把警語放在區塊之前」能讓模型不混用跨串內容。這個假設**沒有實測支撐**，只是 prompt 工程的一般經驗。若實測發現群組草稿開始出現張冠李戴，正確反應是把 `DRAFT_CROSS_THREAD_ENABLED` 關掉、改在 UI 提示「這串還沒人回，你可能想勾 Reference Space」，而不是繼續加強語氣。這也是我建議把它做成開關的理由。

---

## 明確主張「維持現狀」的部分

1. **群組且 thread ≥ 2 的路徑，一個字都不要改。** 它目前是對的，而且是這個功能最常走的路徑。任何改動都是純風險。
2. **摘要工作台不要改成錨點式。** 摘要沒有錨點，`fetch_recent_messages(N)` 對它是正確的撈法。使用者說的「連貫性」不該被理解成「兩邊用同一個函式」。
3. **`IMAGE_BUDGET_TOKENS_DRAFT`（8000）與 `IMAGE_MAX_COUNT`（8）不要動。** 問題在取樣範圍不在預算（C-5）。
4. **不要新增前端旋鈕。** 第一版全走 config 常數（C-7、2.7）。
5. **不要因為這件事開始做相關性檢索。** 只用結構規則（C-9）。

---

## 直接回答你列的 5 個疑慮

| # | 你的問題 | 我的答案 |
|---|---|---|
| 1 | 時間窗要多大？要跟摘要一樣嗎？ | **不要一樣。** 摘要的 N 是「產出涵蓋範圍」，草稿的 K 是「輸入理解深度」，共用會讓調摘要長度靜默劣化草稿。建議前 15／後 10，**外加 48h 上界與 6 則保底**——形狀（雙向 + 時間上界）比大小重要得多。 |
| 2 | 群組薄 thread 要不要補？會不會稀釋？ | **要補，但用獨立區塊 + 警語 + 更小的窗（8 則）+ config 開關**，不能跟私訊走同一條路徑。理由：群組已經有 Reference Space 這個使用者控制的補脈絡機制，私訊沒有——私訊缺的是基本盤，群組缺的是補充。 |
| 3 | 圖片會不會擠掉文字、選到不相關的圖？ | **不會擠掉文字**（圖文分開傳，預算獨立）。**會選到不相關的圖**，而且原因是 `attachments.collect()` 跟文字脈絡共用 `thread_msgs` 這個變數——放大文字就靜默放大了圖片母體。解法是解耦成 `image_messages`（排除 cross_thread 與 after），並明示 `scan_recent`。 |
| 4 | 成本？claude_cli vs gemini 敏感度不同？ | **方向可能是反的。** gemini 免費層算的是每日請求數不是 token，脈絡變大邊際成本為零；脈絡不足導致重跑才真的燒配額。claude_cli 吃訂閱，多 1~2k tokens 與既有的 19k 快取問題不同量級。真正的約束是**訊噪比與延遲**，不是錢。 |
| 5 | 有沒有更好的框架（錨點前後 K 則）？ | **有，就是它**，而且理由比你想的更強：錨點不一定是最新（收件匣積壓時「最近 N 則」可能整個錯開）、錨點之後的訊息有獨立價值（偵測已被回答）、以及可重現性（草稿存進 DB，重跑要能得到同樣輸入）。 |
