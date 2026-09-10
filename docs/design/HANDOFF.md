# 交接：ChatPulse 介面改版的下一個 session

> 建立於 2026-09-10。範圍是分支 `feature/ui-redesign-evidence-first` 上的介面改版。
> 設計決策的單一事實來源是同目錄的 `2026-09-09-ui-redesign.md`，這份只講「接下來要做什麼」。
> 專案層級的交接（安裝、AI 配額、Google Chat API 的坑）仍看 `docs/HANDOFF.md`。

---

## 直接可用的提示詞

把下面整段貼進新 session：

````text
接手 ChatPulse 的介面改版。專案在 /Users/cheng/google-chat-bot。

先讀 docs/design/HANDOFF.md（交接：現況、兩個已定位的 bug、未完成工項、
不要破壞的東西、驗證陷阱）。設計決策的單一事實來源是
docs/design/2026-09-09-ui-redesign.md，需要時再查對應章節，不必全讀。

現況：分支 feature/ui-redesign-evidence-first，18 個 commit，main 未動，
工作區乾淨。八個 Phase 實作完成，兩個回報的 bug 已修，291 項測試全綠
（其中 31 項是元件測試）。

這次要做的，依序：

1. 元件測試還缺規格 §15.4 的前兩條（HANDOFF 未完成工項第 4 項）：
   送出流程的確認框，以及 Sepia 三態。這兩條的價值最高——「送出了不可
   撤回的訊息」與「以為 Sepia 生效其實沒有」是這個產品最貴的兩個錯誤。

2. 在真瀏覽器上覆驗 Bug 2（HANDOFF 第七節第 1 點）。上一輪只有 jsdom
   的證據，沒有真瀏覽器實測。

3. 有餘力再看未完成工項 5（code_terms）與「其他小項」，或動 3b 的
   檔案大小帳（拆 DraftReplyWorkspace 要照規格 §9.2 的清單）。

工作方式：
- 每一項獨立 commit，Conventional Commits、繁體中文。
- 每個 commit 前跑 typecheck ＋ test，並透過 scripts/webapp.py 重建
  （單獨 npm run build 會讓 dist 被判 stale，指令在 HANDOFF 第四節）。
- 宣告修好之前要有本 session 的實際證據：測試輸出或瀏覽器實測。
  新測試寫完做一次反向對照（把修復還原，確認測試真的會紅）。
  用瀏覽器探針時注意 HANDOFF 第四節列的三個陷阱，特別是
  「回報 0 次必須有正對照」與「DOM 探針要限縮在非 inert 的那個 pane」。
- HANDOFF 第三節那七條約束是踩過坑寫出來的，動到相關程式碼前先看一眼。
````

---

## 30 秒現況

> **2026-09-10 更新（第二個 session）**：下面第一節的兩個 bug **都已修掉**，
> 未完成工項 1～4 也做完了。這一段與各節的狀態標記都是那一輪之後的現況。

- **分支**：`feature/ui-redesign-evidence-first`，**18 個 commit**，**`main` 未動**。
- **狀態**：規格書的八個 Phase 全部實作完成。兩個已回報的 bug 已修，有元件測試蓋住。
- **測試**：**291 項 / 22 檔**全綠。`npm --prefix dashboard/frontend run test`
  - 其中 **31 項是元件測試**（jsdom project，`*.test.tsx`），其餘仍是純函式（node）。
- **啟動**：`./chatpulse.sh web`。注意 **`chatpulse.sh` 不吃 `--port`**，一律起在 8000
  （前一版交接寫的 `--port 8010` 是錯的，那個參數會被忽略）。
- **待辦**：未完成工項只剩第 5 項與「其他小項」，另有 3b 的檔案大小帳。

```bash
# 接手後先跑這三個確認基準
npm --prefix dashboard/frontend run typecheck        # 應為零錯誤
npm --prefix dashboard/frontend run test             # 應為 291 passed / 22 files
.venv/bin/python -c "import sys; sys.path.insert(0,'scripts'); import webapp; print(webapp.frontend_state())"
# 期望輸出：ready
```

---

## 一、兩個已定位的 bug（**兩個都已修復**，2026-09-10）

> 這一節保留原本的根因分析，因為它解釋了「為什麼那樣修」。
> 修復狀態與實際做法補在各自的「已修復」段。

### Bug 1：從摘要工作台產生的草稿，在待處理分頁卻顯示「退回待處理」

> **已修復** — commit `8e89c22`。判準抽成 `store/mentions.ts` 的 `isOutstanding()`，
> 原本分歧的三處都改用它。`MentionInbox.test.tsx` 有 5 項元件測試蓋住
> （含「按下去送出的是 resolved 不是 pending」），`mentions.test.ts` 另加 4 項。
> 反向對照做過：把修復還原，那兩項立刻紅在「找不到『標記已處理』按鈕」。

**現象**（使用者回報）：摘要工作台按「產生回覆草稿」→ 跳到收件匣 → 那則出現在「待處理」分頁，但按鈕寫的是「退回待處理」。

**根因：「什麼算待處理」有兩份定義，其中一份漏掉了 `manual`。**

| 位置 | 判準 | 有沒有把 `manual` 算進待處理 |
| :--- | :--- | :--- |
| `core/repository.py:404-413`（後端查詢） | `state IN ('pending', 'manual')` | ✅ 有 |
| `dashboard/frontend/src/store/mentions.ts:225-227`（分頁歸類） | `item.state === 'pending' \|\| item.state === 'manual'` | ✅ 有 |
| `dashboard/frontend/src/store/mentions.ts:202-204` 的 `bucket()`（計數） | `state === 'resolved' ? 'resolved' : 'pending'` | ✅ 有 |
| **`dashboard/frontend/src/components/MentionInbox.tsx:235`（按鈕分支）** | **`mention.state === 'pending'`** | ❌ **沒有** |

三處把 `manual` 算成待處理，只有按鈕那一處沒有。於是 `manual` 落進 else 分支，拿到「退回待處理」。

**資料佐證**（2026-09-10 實查 `data/chatpulse.db`）：

```
各 state 筆數：manual 1、pending 2、resolved 35
最近的 manual：(65, 'manual', '（未命名空間）', '陳柏元', '2026-09-10T02:16:39Z')
```

id=65 正是截圖那一則，而頂列 badge 顯示「待處理 3」＝ 2 pending ＋ 1 manual，與 `bucket()` 一致。

**這不只是文案錯。** 按下「退回待處理」會呼叫 `setMentionState(id, 'pending')`，把它從 `manual` 改成 `pending`——「自選對話」這個來源標記就被無聲抹掉了，而且不可逆（沒有把 pending 改回 manual 的路徑）。

**建議修法**：把判準抽成一個 export，讓三處不可能再漂移。

```ts
// store/mentions.ts —— 新增一個 export（`MentionStateValue` 同檔已從 lib/types 匯入）
/**
 * 這則還沒處理完嗎。
 *
 * `manual`（從摘要工作台按「產生回覆草稿」挑的）算待處理——使用者按下那個
 * 按鈕的意思就是「我要回這則」，與被 @ 一樣是一件待辦。後端的 list_mentions
 * 與 count_mentions 都是這樣算的，前端三處判準必須跟它一致。
 */
export function isOutstanding(state: MentionStateValue): boolean {
  return state !== 'resolved'
}
```

三處都改用它：

| 位置 | 改法 | 行為變化 |
| :--- | :--- | :--- |
| `MentionInbox.tsx:235` | `isOutstanding(mention.state) ? （標記已處理） : （退回待處理）` | **這是修 bug 的那一處** |
| `store/mentions.ts:225-227` 的 `selectMentionsByState` | 內部條件換成 `isOutstanding(item.state)` | **無**——只是把定義集中，不要動它的**簽章與回傳**（`mentions.test.ts` 有 6 項打在上面） |
| `store/mentions.ts:202-204` 的 `bucket()` | 換成 `isOutstanding(state) ? 'pending' : 'resolved'` | **無** |

換句話說：**可以改那兩處的實作，不要改它們的介面。** 目的是讓「什麼算待處理」只剩一個定義，下次不會再漂移。

**驗收**
- [ ] 摘要工作台按「產生回覆草稿」→ 收件匣那則顯示「標記已處理」，不是「退回待處理」
- [ ] 按下「標記已處理」後它移到「已處理」分頁，且該處按鈕才是「退回待處理」
- [ ] 頂列 badge 的數字與「待處理」分頁的筆數一致（現在也一致，不要改壞）
- [ ] `mentions.test.ts` 的 6 項仍全綠；新增至少一項「manual 顯示為待處理」的測試

**已經檢查過的同族位置**：`grep -rn "state === 'pending'" dashboard/frontend/src` 另外命中
`DraftReplyWorkspace.tsx:178` 與 `:183`（草稿工作區標題列的狀態 badge）。**那兩處是對的，不要一起改。**
它刻意做成三分：只有 `pending` 帶訊號色，`manual` 與 `resolved` 都安靜下來，再用文字
（「自選對話」／「✓ 已處理」）分辨——因為 `--verified` 與 `--signal` 是同一個色，
不這樣做的話兩種狀態會長得一模一樣。改判準的時候把它排除在外。

---

### Bug 2：設定頁點了五個分頁，要按五次「關閉」才出得去

> **已修復** — commit `8e71ab9`。照下面建議的兩步做：分頁列改 replace、
> router 新增 `previousHash`、`openedAt` 與整段啟發式條件刪掉。
> `SettingsOverlay.test.tsx` 有 8 項元件測試蓋住。反向對照做過，訊息正是回報
> 的症狀：`expected 5 to be +0`（點五個分頁 push 了五筆）、
> `expected '#/settings/spaces' to be '#/mentions/65'`（按一次只退一個分頁）。
>
> **下面「陷阱」段講的那件事是真的**：設定開著又開命令面板時，按一次 Esc
> 會同時關掉面板與設定（面板的 Esc 是 React 合成事件，處理完原生事件仍會冒泡
> 到設定掛在 window 上的監聽器）。同一個 commit 一併修掉，守衛與
> `useGlobalHotkeys` 同一條規則：面板開著時設定不接鍵盤。

**現象**（使用者回報）：在設定中心切換多個分頁後，每按一次「關閉」只退回上一個分頁。

**根因：分頁切換用裸 `<a href>`，每次都往瀏覽器歷史 push 一筆；而「關閉」只 `history.back()` 一步。**

- `components/settings/SettingsOverlay.tsx:116-117` —— 分頁是 `<a href={hashForSettings(item.id)}>`。點擊會改 `location.hash`，瀏覽器**推入一筆新歷史**。
- `components/settings/SettingsOverlay.tsx:60-68` —— `close()` 呼叫 `window.history.back()`，只退一步，於是退到上一個設定分頁。

點 N 個分頁 → N 筆歷史 → 要按 N 次。

**建議修法（兩步，缺一不可）**

**① 分頁切換改成 replace。** 切換設定分頁不是「值得用返回鍵走回去」的導覽，它應該只占一筆歷史。保留 `href` 讓中鍵開新分頁與螢幕閱讀器仍然正確，但攔截左鍵：

```tsx
<a
  href={hashForSettings(item.id)}
  onClick={(event) => {
    // 讓中鍵／⌘＋點擊維持瀏覽器原生行為
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return
    event.preventDefault()
    navigate(hashForSettings(item.id), { replace: true })
  }}
  aria-current={active ? 'page' : undefined}
>
```

**② `close()` 不要再靠 `history.length` 猜。** 現在的條件是
`window.history.length > openedAt.current || window.history.state !== null`——
改成 replace 之後 `history.length` 不再成長，這個條件會變成永遠 false，
於是每次關閉都掉到 fallback 的 `#/summary`，**使用者會離開他原本在看的 Space／Mention**。

正確做法是讓 router 記住「進設定之前在哪」。`router/useRouter.tsx` 要動三個地方：

```ts
// ① import 補上 useRef（目前那一行只有 createContext/useCallback/useContext/
//    useEffect/useMemo/useState/ReactNode，沒有 useRef）

// ② RouterValue 介面加一個欄位（目前只有 route 與 navigate）
interface RouterValue {
  route: Route
  navigate: (hash: string, options?: NavigateOptions) => void
  /** 進入設定覆蓋層之前所在的位置。直接貼設定連結進來時是 '#/summary' */
  previousHash: string
}

// ③ RouterProvider 內部，放在 `const route = useMemo(...)` 之後
//    （route 與 hash 在那個 scope 都拿得到）
const lastNonSettings = useRef('#/summary')
useEffect(() => {
  if (route.section !== 'settings') lastNonSettings.current = hash || '#/summary'
}, [route.section, hash])
// 併進 value 的 useMemo，相依加上 lastNonSettings.current
```

`close()` 就變成：

```ts
const close = () => navigate(previousHash, { replace: true })
```

**要帶 `{ replace: true }`。** 不帶的話會再 push 一筆，於是「進設定 → 關閉 → 按瀏覽器返回鍵」
又回到設定裡——驗收條件第 2 條（返回鍵一次離開設定）會過不了。用 replace 之後，
整段設定操作在歷史上只留下一筆，關閉就是把那一筆換回原本的位置。

這個做法完全不依賴 `history.length` 或 `history.state` 的啟發式，也就順手解掉上面說的
「條件永遠 false」問題——**`openedAt` 這個 ref 與整段條件式可以一併刪掉**。

**驗收**
- [ ] 進設定 → 依序點五個分頁 → **按一次「關閉」就回到原本的位置**（原本在哪個 Space／Mention 就回哪）
- [ ] 在設定中按瀏覽器返回鍵，一次就離開設定（不是逐個分頁退）
- [ ] 直接貼 `#/settings/personas` 開新分頁 → 按「關閉」落到 `#/summary`，不是白畫面
- [ ] ⌘K 命令面板跳到某個設定分頁後，「關閉」行為一樣正確
- [ ] `Esc` 與「關閉」按鈕行為一致

**陷阱**：`SettingsOverlay` 的 `Esc` 監聽掛在 `window` 上，而命令面板也監聽 `Esc`。目前面板開著時全域快捷鍵整組停用（`useGlobalHotkeys` 有擋），但設定的 `Esc` 是**元件自己**掛的、不走那條路。改 `close()` 時順手確認「設定開著又打開命令面板，按 Esc 只關面板」。

---

## 二、未完成工項（依價值排序）

> **2026-09-10 進度**：1、2、3、4 已完成，剩 5 與「其他小項」。3b 的帳有還一部分。

### 1. 虛擬清單的 roving tabindex（規格 §10.5）— **已完成**（commit `880e4ea`）

實作照下面寫的做了，另外踩到三個下面沒預料到的坑，程式碼裡都留了註解：

1. **roving tabindex 配虛擬滾動有個洞**：active 那一列捲出可視範圍就不在 DOM 裡，
   於是整份清單沒有任何 `tabIndex=0` 的節點、**Tab 進不去**。解法是這時把 tab
   停留點讓給第一個還掛著的列。
2. **取焦要等目標列掛出來**：`scrollToIndex` 之後那一列不保證在同一個 tick 就存在，
   所以用不設相依的 `useLayoutEffect` 每次 render 試一次，取到才收手。
3. **改名鈕不能放進 `role="option"` 裡**（option 內不該有可互動元素）。`li` 改成
   `role="none"` 只當定位容器，option 是它裡面那一層，改名鈕是 option 的兄弟。

測試：`lib/listNavigation.test.ts` 8 項（純函式）＋ `SpaceList.test.tsx` 10 項。
jsdom 沒有佈局、真虛擬清單一列都掛不出來，所以測試裡把 virtualizer 換成
「固定只掛前 13 列」的假件，保留「只有一部分列在 DOM 裡」這個唯一相關的性質。

<details>
<summary>原本的規劃內容（保留供對照）</summary>

436 筆 Space 目前仍是**逐個 Tab**，鍵盤使用者要走很久。⌘K 命令面板提供了替代路徑，所以不是死路，但這條該補。

做法（規格 §10.5 有完整說明）：`<ul role="listbox">` ＋ `<li role="option" aria-selected>`，`tabIndex` 只有 active 那列是 0；`↑↓ Home End PageUp PageDown Enter`；移動時先 `virtualizer.scrollToIndex({ align: 'auto' })`（**不要 `center`**）再取焦。按鍵→index 的計算抽到 `lib/listNavigation.ts` 純函式測。

**不要用 `aria-activedescendant`**：它要求被指向的節點常駐 DOM，而虛擬滾動只掛可視範圍 ＋ overscan 12 列。

檔案：`components/SpaceList.tsx`。注意它同時服務單選（摘要）與複選（Reference Space）兩種模式。

</details>

### 2. `?merge=` 的 URL 同步 — **已完成**（commit `796be65`）

照原訂做法做的（新增 `setMergeIds`，不動 `toggleMerge`）。兩件原本沒寫到、但會咬人的事：

- **網址不表達「只勾了一則」。** `hashForMentions` 兩則以上才帶 `merge`
  （`route.test.ts` 有一條守著這個設計決定）。所以 URL→store 反向同步時，
  網址上沒有 merge **不可以**清掉單獨一則的勾選——照清的話，使用者勾第一則的
  瞬間它會自己彈回去。突變測試確認過這條守衛。
- **`App.tsx` 的 `onMergedGenerate` 原本導航時丟掉 `mergeIds`。** 加了反向同步
  之後，那會在按下「合併產生草稿」的瞬間把勾選清空，`DraftReplyWorkspace` 的
  `activeMergeIds` 跟著變空——合併就散了。已一併改成把 mergeIds 帶進網址。

測試：`MentionInbox.test.tsx` +5、`useRouteSync.test.tsx` +6（後者順便把紅線 4
的去重鎖住，含正對照）。

### 3. `QuickReplySettings` 與 `ReplyDefaultsPage` 的共用元件 — **已完成**（commit `14579bf`）

範圍比原訂的大一些：兩邊**讀寫的是同一份 store**，所以重複的不只是
`SelectContent`，連 `toneItems`／`personaValue` 這些推導也逐字重複。抽出來的是
四個完整元件（`ToneSelect`／`PersonaSelect`／`PromptSelect`／`PersonaNotice`），
呼叫端只給真正有差異的 trigger id、寬度、`disabled`。

行數 365 → **200**、312 → **139**，新檔 237。

重構的驗證方式值得照抄：**先寫特徵測試，拿它跑重構前的程式碼確認會過，
再跑重構後。** `replyControls.test.tsx` 的 6 項在前後兩版都全過——這才是
「外部行為沒變」的證據，只跑重構後的版本證明不了任何事。

### 3b. 檔案大小門檻沒達成（規格 P4 的驗收條件，前一個 session 漏了沒檢）
規格 §14 的 P4 有一條「**全域最大檔 < 250 行**」（`lib/types.ts` 是認可的例外），但沒有實際跑過那條檢查就結案了。現況：

```bash
find dashboard/frontend/src -name '*.tsx' -o -name '*.ts' | grep -v test | xargs wc -l | sort -rn | head
```

| 檔案 | 行數（2026-09-10 更新） |
| :--- | ---: |
| `lib/types.ts` | 560（純型別，規格認可的例外） |
| `components/DraftReplyWorkspace.tsx` | **478** |
| `App.tsx` | 428 |
| `store/replySettings.ts` | 413（store，本次未動） |
| `components/SummaryWorkspace.tsx` | 371 |
| `lib/evidence.ts` | 360 |
| `store/draft.ts` | 341 |
| `lib/api.ts` | 321 |
| `components/MentionInbox.tsx` | 304（本輪 +40：合併寫回網址與註解） |
| …另有 2 個介於 256–281 之間 | |

`QuickReplySettings`（365 → 200）與 `ReplyDefaultsPage`（312 → 139）已經因為
工項 3 掉到門檻以下。仍超標的還有 8 個（不含 `types.ts`）。

**這不是急件**——477 行的 `DraftReplyWorkspace` 已經比改版前的 656 行好很多，也不影響功能。但規格說要拆而沒拆，該記在帳上。真要動的話，規格 §9.2 有完整的拆檔清單（`MentionSourceCard`／`ReferenceSpacePicker`／`CodeRefPicker`／`DraftSetupPanel`／`DraftOutputPane`／`DraftReplyEditor`／`SendReplyConfirm`）。

順序建議：**先修兩個 bug、先補元件測試，再談拆檔**。沒有元件測試的情況下拆 477 行是在沒有安全網的高處走。

### 4. 元件層測試（規格 §15.4）— **環境已建好**（commit `64d6733`），還有兩條沒寫

工具決策照規格：`@testing-library/react` ＋ `jsdom`，用 `test.projects` 讓
`*.test.tsx` 走 jsdom、`*.test.ts` 維持 node。**既有純函式測試的執行環境完全沒變。**

目前 31 項元件測試，分佈：`MentionInbox` 10、`SettingsOverlay` 8、`SpaceList` 10、
`useRouteSync` 6、`replyControls` 6（跨兩個 project 合計 291 項 / 22 檔）。

規格 §15.4 列的四條裡，**還沒寫的是第 1 與第 2 條**：

1. **送出流程**：`meta.answering` 有 3 則時確認框要列出那 3 則的寄件人與時間；
   空白／串流中送出鈕 disabled；`send` 拋錯不關對話框。
2. **Sepia 三態**：`polished === false` 時 `fallback_reason` 必須用 `getByText`
   找得到（**不是** `toHaveAttribute('title')`）。

這兩條的價值比已寫的還高——「送出了不可撤回的訊息」與「以為 Sepia 生效其實
沒有」是這個產品最貴的兩個錯誤。下一輪優先做它們。

**寫元件測試時記住兩件本輪學到的事**：

- **每個「0／沒發生」的斷言都要配一條正對照。** 例：「切分頁 +0 筆歷史」旁邊
  放「一次 push 導覽 +1」，證明 `history.length` 在 jsdom 真的會動。
- **新測試寫完要做反向對照**：把被測的修復還原，確認測試真的會紅。本輪四次都做了，
  其中兩次抓到「斷言其實沒在咬」的問題。

### 5. `code_terms` 手動指定檢索關鍵字
規格 §1.4 說明了為何刻意不做：它要動 `store/draft.ts` 的 `generate()` 請求組裝，而那是既有測試覆蓋最密集的一段，收益（一個次要輸入框）與風險不成比例。要做的話請一併補 store 測試。

### 其他小項
- `title=` 還剩 12 處（規格 §10.3 逐條列了改法）。守門測試 `lib/tokens.test.ts` 已鎖住**不得增加**，所以不會惡化。
- 768–1024 沒做成規格寫的「主從切換」。實測兩欄並存可用、無功能損失，所以沒為它多加一種版面狀態。

---

## 三、不要不小心破壞的東西

這幾條是踩過坑寫出來的，程式碼裡都有註解說明理由：

1. **串流狀態住在 store，切頁籤不中止生成。** `DraftReplyWorkspace.tsx` 與 `SummaryWorkspace.tsx` 都刻意**不在 unmount 時 abort**。（`docs/api-contract.md:684-685` 寫的「unmount 要 abort」與程式碼相反，那份文件還沒更新。）
2. **`store/draft.ts` 的 `reset()` 不清跨 Mention 的偏好**（Reference Space、參考專案、口氣、Persona…）。切一則就洗掉會很難用。
3. **`meta.answering` 才是送出時要結掉哪幾則的依據**，不是送出前的勾選。伺服器會擋掉不合規的項目。
4. **`useRouteSync` 的去重不可拿掉。** `mentions.select` 有個副作用是清空 `external`，而摘要工作台建立的草稿目標正是靠 `external` 撐著——少了去重，切過去的瞬間工作區就會被清成空白。**Bug 1 的那條路徑就是走這裡，改的時候特別小心。**
5. **`pinned_space_ids` 送 `null` 是「不改」不是「清除」。** 取消最後一個釘選要送 `[]`。（與四個回覆設定欄位相反，`docs/api-contract.md:130` 有對照表。）
6. **合併回覆的規則有前後端兩份實作**（`resolve_merge_targets` 與 `lib/merge.ts`），改一邊要改兩邊。
7. **守門測試 `lib/tokens.test.ts`** 會擋下具名色、任意字級、新增的 `title=`、Tooltip、硬編碼色碼。它不是形式主義——那五條各自對應一個實際發生過的問題。

---

## 四、驗證方法與已知的探針陷阱

改版期間用瀏覽器探針驗證時踩到三個坑，都值得記住：

1. **「0 次」必須有正對照。** 量「切頁籤沒有多打 API」時，`fetch` 攔截器回報 0 —— 但那也可能是攔截器根本沒生效。補上「按強制刷新應該記到 1 次」的正對照之後，那個 0 才有意義。
2. **DOM 探針要限縮範圍。** 兩個工作台**常駐掛載**、設定是**覆蓋層**，所以 `document.querySelector` 會選到背景那一份。要先取「沒有 `inert` 屬性的那個 pane」再往下找。我曾因此誤判釘選功能壞掉，實際上是選到了背景的 SummaryHistory。
3. **Tailwind 會樹搖沒被使用的 token。** 建置產物裡找不到 `--container-rail` 不代表壞了，只代表還沒有人用它。要驗 utility 有沒有生成，得在原始碼裡真的用一次再建置。

**建置一律透過 `scripts/webapp.py`**（它才會蓋章 `.buildinfo.json`），單獨 `npm run build` 會讓 dist 永遠被判 stale：

```bash
.venv/bin/python -c "import sys; sys.path.insert(0,'scripts'); import webapp; \
  print(webapp.build_frontend(webapp.find_npm()), webapp.frontend_state())"
# 期望輸出：True ready
```

還有一個實際踩到的：**改完原始碼後第一次建置，Tailwind 的 Vite plugin 可能沿用上一輪的 class 候選集**。我曾在暫時加了測試用 class 又移除之後，建置出來的 CSS 仍含那些 class；再建一次才乾淨。**不要只看建置成功訊息，要 grep 產物確認。**

---

## 五、檔案地圖（改版新增的部分）

```
docs/design/
  2026-09-09-ui-redesign.md   設計規格書（單一事實來源，含八個 Phase 的驗收條件）
  HANDOFF.md                  這份

dashboard/frontend/src/
  app/                 PulseMark（脈搏識別）、SmallScreenNotice（<768 說明頁）
  router/              useRouter（薄層 hash router）、useRouteSync（URL → store 單向同步）
  hooks/               useBreakpoint（版面斷點）、useGlobalHotkeys（全域快捷鍵）
  components/
    evidence/          EvidenceColumn／EvidenceList／EvidenceDrawer（改版主角）
    settings/          SettingsOverlay ＋ 七個設定頁 ＋ DiagnosticsPage
    draft/             QuickReplySettings（工作區的「這一次」設定）
    CommandPalette.tsx ⌘K
  lib/
    evidence.ts        SseMeta → 證據項（純函式，24 項測試）
    route.ts           hash 解析與組裝（純函式，24 項測試）
    commands.ts        命令面板資料層（純函式）
    hotkeys.ts         快捷鍵判斷（純函式）
    tokens.test.ts     設計 token 守門測試（含三條正對照）
  store/
    ui.ts              抽屜開合（分斷點記憶）、命令面板開合
    usage.ts           Token 用量（天數可切）
```

---

## 六、commit 一覽

| commit | 內容 |
| :--- | :--- |
| `abb5013` | 設計規格書 |
| `bb385d4` | P0 設計 token 地基與脈搏識別 |
| `757ad07` | P1 hash 路由與深連結 |
| `472f0c7` | P2 兩個工作台常駐掛載 |
| `7d51444` | P3 獨立設定中心、釘選 Space |
| `e3cbed3` | P4 證據欄 |
| `02ab318` | P5 色彩與排版退役、守門測試 |
| `323d691` | P7 響應式（證據抽屜、小螢幕說明頁） |
| `e7fecdd` | 無障礙 landmark、skip link、文案去術語化 |
| `164ddc1` | P6 命令面板與快捷鍵 |
| `414d54c` | 規格書回填實作進度 |
| `5a65de9` | 這份交接文件 |
| `8e89c22` | fix：自選對話顯示成「退回待處理」（Bug 1） |
| `8e71ab9` | fix：設定頁要按 N 次關閉（Bug 2）＋ Esc 同時關掉面板與設定 |
| `64d6733` | test：元件測試環境（jsdom project）＋ 兩個 bug 的場景 |
| `880e4ea` | feat：虛擬清單 roving tabindex（工項 1） |
| `796be65` | feat：`?merge=` URL 同步（工項 2） |
| `14579bf` | refactor：兩處回覆設定共用下拉元件（工項 3） |

分支尚未推送，`main` 未動。要合併時照專案慣例 `git merge --no-ff`。

---

## 七、下一輪接手的人請注意

1. **Bug 2 沒有在真瀏覽器上驗過。** 那一輪的 Playwright 是全新 profile、停在登入頁，
   claude-in-chrome 擴充又沒連上，所以證據全部來自 jsdom 的元件測試。jsdom 的
   history 實作與真瀏覽器不完全相同——**點五個分頁按一次關閉**這件事，值得你在
   自己已登入的瀏覽器上再點一次確認。其餘幾項（Bug 1、roving tabindex、`?merge=`、
   共用元件）也都只有測試證據。
2. **`chatpulse.sh` 不吃 `--port`。** 前一版交接寫的 `--port 8010` 會被忽略，一律起
   在 8000。
3. **zsh 預設 `noclobber`**：腳本裡用 `>` 覆寫已存在的檔案會失敗（訊息是
   `file exists`）。要覆寫用 `>|`。這個坑在本輪的暫存檔操作上踩到一次。
