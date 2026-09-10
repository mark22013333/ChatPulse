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

現況：分支 feature/ui-redesign-evidence-first，main 未動，工作區乾淨。
八個 Phase 實作完成，兩個回報的 bug 已修，五項未完成工項全部完成，
§10.6 的串流無障礙宣告與 §10.3 的 title 清理也做完了。
測試 389 項全綠：node 285 項（19 檔純函式）＋ jsdom 104 項（9 檔元件）。
瀏覽器 E2E 29 項全綠。

這次要做的，依序：

1. **規格 §11.1 的快捷鍵表只實作了五分之二。** 有的：⌘K、⌘J、Esc、
   斜線、問號。缺的：`g s`／`g m`／`g ,`／`g h`（兩鍵序列）、左欄清單的
   ↑↓、⌘Enter（開始生成）、⌘.（停止串流）、⌘⇧C（複製）、`[`／`]`
   （上一則／下一則 Mention）。這是目前最大的一塊未實作規格，而且它
   卡住另一件事——見 §10.6 那一節的說明。

2. 3b 剩下的檔案大小帳（見那一節的表）。規格 §9.1 只為
   `CodeProjectSettings.tsx` 留了拆檔計畫，其餘八個超標檔沒有規格依據，
   動之前請先看那一節的建議。

3. 「其他小項」只剩 768–1024 的主從切換（`title=` 已清完）。

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

> **2026-09-10 更新（第二、三輪）**：下面第一節的兩個 bug **都已修掉**，
> 未完成工項 **1～5 全部完成**，規格 §15.4 的四條元件測試也補齊，另外新增了
> 瀏覽器 E2E。剩下的只有 3b 的部分檔案大小帳（見那一節）。

- **分支**：`feature/ui-redesign-evidence-first`，**`main` 未動**。
  commit 數用 `git rev-list --count main..HEAD` 查——寫死在這裡的話，
  下一個「更新這份文件」的 commit 自己就會讓它過期。
- **狀態**：八個 Phase 實作完成、兩個回報的 bug 已修、五項未完成工項全部完成、
  §10.6 串流宣告與 §10.3 的 title 清理完成。
- **測試**：
  - 單元／元件：**389 項 / 28 檔**全綠（`npm --prefix dashboard/frontend run test`）
    - node project：**285 項 / 19 檔**（純函式，`*.test.ts`）
    - jsdom project：**104 項 / 9 檔**（元件，`*.test.tsx`）
  - 瀏覽器 E2E：`tests/e2e/test_ui_redesign.cjs` **29 項**全綠（真 Chromium）。
    五支 `.cjs` 一起跑用 `.venv/bin/python tests/e2e/run_browser.py`。
- **啟動**：`./chatpulse.sh web`。注意 **`chatpulse.sh` 不吃 `--port`**，一律起在 8000
  （第一版交接寫的 `--port 8010` 是錯的，那個參數會被忽略）。

```bash
# 接手後先跑這幾個確認基準
npm --prefix dashboard/frontend run typecheck        # 應為零錯誤
npm --prefix dashboard/frontend run test             # 應為 354 passed / 26 files
.venv/bin/python -c "import sys; sys.path.insert(0,'scripts'); import webapp; print(webapp.frontend_state())"
# 期望輸出：ready

# 瀏覽器 E2E（先設 CHATPULSE_SESSION，那條路零資料庫寫入，說明見
# tests/e2e/e2e_browser.cjs 開頭）
node tests/e2e/test_ui_redesign.cjs                  # 應為 26/26
```

### 這一輪最值得記住的一件事

**jsdom 的綠燈證明不了瀏覽器行為。** 我修「Esc 同時關掉命令面板與設定」時
用 `paletteOpen` 當守衛，元件測試是綠的——但那個測試只 `setState` 了旗標、
沒有掛真正的面板，等於在測「守衛讀不讀 store」。真瀏覽器 E2E 第一次跑就
證明修法無效：面板的 `close()` 是同步的 zustand set，事件冒泡到設定的 window
監聽器時 `paletteOpen` 已經變回 false。**寫元件測試時，凡是涉及事件冒泡順序、
瀏覽器歷史、真實焦點的行為，要嘛掛上真正的相關元件，要嘛就交給 E2E。**

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
> 會同時關掉面板與設定。但**第一次的修法是錯的**——commit `8e71ab9` 只用
> `paletteOpen` 當守衛，元件測試綠燈，真瀏覽器 E2E 卻證明無效：面板的
> `close()` 是同步的 zustand set，事件冒泡到設定的 window 監聽器時
> `paletteOpen` 已經變回 false。commit `adf7c84` 補上 `event.defaultPrevented`
> 才真的修好。**兩道守衛都要**：`defaultPrevented` 管「內層已經處理掉這個
> 按鍵」，`paletteOpen` 管「面板開著但按鍵不是它處理的（焦點在輸入框外）」。

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

### 3b. 檔案大小門檻（規格 P4）— **兩個大檔已拆，剩九個**

規格 §14 的 P4 有一條「**全域最大檔 < 250 行**」（`lib/types.ts` 是認可的例外）。
這一輪拆掉了規格**有給拆檔計畫**的那兩個：

| 檔案 | 拆前 | 拆後 | commit |
| :--- | ---: | :--- | :--- |
| `components/DraftReplyWorkspace.tsx` | 478 | 搬到 `components/draft/`，**86** ＋ 八個兄弟檔（46–106） | `fa268d8` |
| `App.tsx` | 428 | `app/AppShell.tsx` **200** ＋ `app/TopBar.tsx` 178 ＋ `app/useBootstrap.ts` 105 | `748f94a` |
| `components/draft/QuickReplySettings.tsx` | 365 | **200**（抽出共用下拉） | `14579bf` |
| `components/settings/ReplyDefaultsPage.tsx` | 312 | **139**（同上） | `14579bf` |

**還超標的九個（2026-09-10 實測）**：

| 檔案 | 行數 | 規格有給拆法嗎 |
| :--- | ---: | :--- |
| `lib/types.ts` | 560 | — 認可的例外（純型別、鏡射後端契約） |
| `store/replySettings.ts` | 413 | ❌ 沒有 |
| `components/SummaryWorkspace.tsx` | 383 | ❌ 沒有 |
| `store/draft.ts` | 370 | ❌ 沒有 |
| `lib/evidence.ts` | 360 | ⚠ §9.2 標 ~240，但沒說怎麼拆 |
| `lib/api.ts` | 321 | ❌ 沒有 |
| `components/MentionInbox.tsx` | 304 | ❌ 沒有 |
| `components/SpaceMessagePreview.tsx` | 287 | ❌ 沒有 |
| `components/CodeProjectSettings.tsx` | 256 | ✅ §9.1：檔名應消失，內容進 `settings/CodeProjectsPage.tsx` ＋ `CodeProjectForm.tsx` |
| `store/mentions.ts` | 251 | ❌ 沒有 |

（`SummaryWorkspace` 與 `draft.ts` 比上一輪各多了十幾行，是 §10.3 的可見文字
與 `code_terms` 加進去的——把 tooltip 改成可見說明本來就會讓檔案變長。）

**建議（下一輪動之前先想一次）**：

1. **`CodeProjectSettings.tsx` 可以照規格拆**（§9.1 有計畫，而 `CodeProjectsPage.tsx`
   目前只是包著它的薄殼）。只超標 6 行，價值不高但有依據。
2. **其餘八個沒有規格依據，我建議不要為了行數而拆。** 三個是 store、兩個是純
   函式 lib——把 zustand store 或 `api.ts` 拆開，換到的是「每個檔都在 250 行以下」
   這個數字，付出的是多一層 import 間接與「這個 action 在哪一半」的認知成本。
   §9.1 訂這條門檻的理由是「兩個 913／650 行的元件難維護」，那個問題已經解掉了。
3. 真要收掉這條驗收條件，建議**改規格**而不是改程式碼：把門檻寫成「**元件**檔
   < 250 行，store 與 lib 另計」，並在 §14 P4 註明理由。那才是誠實的收尾——
   現在的狀態是「規格要求沒達成」，硬拆成達成但更難維護沒有比較好。

### 4. 元件層測試（規格 §15.4）— **已完成**（commit `64d6733`、`4db3303`）

工具決策照規格：`@testing-library/react` ＋ `jsdom`，用 `test.projects` 讓
`*.test.tsx` 走 jsdom、`*.test.ts` 維持 node。**既有純函式測試的執行環境完全沒變。**

目前 31 項元件測試，分佈：`MentionInbox` 10、`SettingsOverlay` 8、`SpaceList` 10、
`useRouteSync` 6、`replyControls` 6（跨兩個 project 合計 291 項 / 22 檔）。

**§15.4 的四條全部寫完了**（commit `4db3303`）：

1. **送出流程** → `components/draft/DraftReplyWorkspace.test.tsx`（29 項）
2. **Sepia 三態** → `components/evidence/EvidenceList.test.tsx`（8 項）
3. **EvidenceList 逐項可讀** → 同上，含「渲染結果裡 `[title]` 選得到 0 個」
4. **命令面板** → `components/CommandPalette.test.tsx`（13 項）

前一版交接把這件事寫錯了，說「還沒寫的是第 1 與第 2 條」——其實第 3、4 條
現有的也只是純函式測試（`evidence.test.ts`／`commands.test.ts`），驗不到渲染。

**寫元件測試時記住兩件本輪學到的事**：

- **每個「0／沒發生」的斷言都要配一條正對照。** 例：「切分頁 +0 筆歷史」旁邊
  放「一次 push 導覽 +1」，證明 `history.length` 在 jsdom 真的會動。
- **新測試寫完要做反向對照**：把被測的修復還原，確認測試真的會紅。本輪四次都做了，
  其中兩次抓到「斷言其實沒在咬」的問題。

### 5. `code_terms` 手動指定檢索關鍵字 — **已完成**（commit `ff601f3`）

規格 §1.4 要求的「自帶 store 測試更新」做到了：`lib/codeTerms.test.ts` 7 項
純函式 ＋ `draft.test.ts` 新增 4 項（含把 `INITIAL` 補上 `codeTerms` 避免測試
互相污染）＋ 元件測試 4 項。

實作上最值得記的一點：**全角逗號與頓號也要當分隔符**。中文輸入法下最容易
打出來的就是它們，而「打了全角逗號結果整串被當成一個詞」是完全看不出來的
失敗——只會得到「什麼都沒命中」。

### 6. 串流的螢幕閱讀器宣告（規格 §10.6）— **已完成**（commit `dbfdc11`）

三層都做了：內容層 `aria-busy`（`Markdown` **絕不加 `aria-live`**）、狀態層
`AppShell` 常駐兩個 sr-only region（`role="status"` ＋ `role="alert"`）只在
狀態機轉換時寫入、節流層每 10 秒一次進度。字串在 `lib/streamAnnouncements.ts`
（22 項純函式測試），接線在 `hooks/useStreamAnnouncer.ts`（12 項）。

**規格有一半做不到，原因記在這裡。** §10.6 說「完成時不搶焦點，改在宣告
文字裡告知快捷鍵」——但 §11.1 表上的導覽鍵（`g s`／`g m`／`[`／`]`／
⌘Enter／⌘.）**都還沒實作**，目前只有 ⌘K／⌘J／斜線／問號／Esc，所以沒有
「跳到產出」的鍵可以告知。沒有的快捷鍵不能拿來宣告，所以改成講 landmark
（「內容在主要內容區」）——那是標準的螢幕閱讀器導覽，不依賴自訂鍵。
**§11.1 補完之後，回來把那句話改成真正的快捷鍵。**

效能上最要小心的一點：`useStreamAnnouncer` **絕對不訂閱 `text` 與 `raw`**。
那兩個每個 chunk 都變，訂閱它們等於讓整個 App 每個 chunk 重繪一次——正是
§9.3 要修掉的問題。它只訂閱布林值，字數等轉換發生的那一刻才 `getState()`
讀一次。`store/draft.ts` 為此多了一個 `hasReplyHeading()`（只做 regex test、
不切字串）。

### 其他小項
- **`title=` 已清完**（commit `26b495c`）。29 → 2，只剩 `SummaryWorkspace` 與
  `draft/SendReplyConfirm` 兩處 `ConfirmDialog` 的 title **prop**（對話框標題，
  不是 tooltip）。`TITLE_BUDGET` 已從 8 檔 12 處收緊到 2 檔 2 處，而且加了一條
  「ConfirmDialog 的 title prop 不會變成 DOM 屬性」的斷言證明那個白名單的理由。
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
| `c5a3889` | 交接文件回填第二輪進度 |
| `4db3303` | test：補完規格 §15.4 的四條元件測試（＋修 CommandPalette 缺 role="combobox"） |
| `fa268d8` | refactor：拆分 DraftReplyWorkspace（478 → 86） |
| `ff601f3` | feat：`code_terms` 手動指定檢索關鍵字（工項 5） |
| `748f94a` | refactor：拆分 App.tsx（→ AppShell／TopBar／useBootstrap） |
| `adf7c84` | test：瀏覽器 E2E（26 項）＋ 修掉它抓到的 Esc 真 bug |
| `9ab3207` | 交接文件回填第三輪 |
| `5880cf8` | test：元件測試逾時放寬到 15 秒（忙碌機器上的假紅燈） |
| `dbfdc11` | feat：串流的螢幕閱讀器宣告（§10.6 三層） |
| `26b495c` | refactor：`title=` 清到只剩兩個對話框標題（§10.3） |

分支尚未推送，`main` 未動。要合併時照專案慣例 `git merge --no-ff`。

---

## 七、下一輪接手的人請注意

1. **Bug 2 已經在真瀏覽器上驗過了**（`tests/e2e/test_ui_redesign.cjs`，26/26）。
   `history.length` 在點過五個設定分頁前後都是 6，按一次「關閉」精確回到
   `#/summary/AAQATjybbSY`，返回鍵一次離開設定。第一輪之所以只有 jsdom 證據，
   是因為 Playwright 用全新 profile 會停在登入頁——現在 `e2e_browser.cjs` 用
   `CHATPULSE_SESSION` 重用既有 session 解決了（而且零資料庫寫入）。
2. **`chatpulse.sh` 不吃 `--port`。** 前一版交接寫的 `--port 8010` 會被忽略，一律起
   在 8000。
3. **zsh 預設 `noclobber`**：腳本裡用 `>` 覆寫已存在的檔案會失敗（訊息是
   `file exists`）。要覆寫用 `>|`。這個坑在第二輪的暫存檔操作上踩到一次。
4. **`tokens.test.ts` 的規則③（`title=` 預算）用的是檔案路徑當 key。** 搬動
   檔案時記得把那一筆一起搬（例：`components/DraftReplyWorkspace.tsx` →
   `components/draft/SendReplyConfirm.tsx`），不然新路徑的預算是 0、直接紅。
5. **寫瀏覽器 E2E 時，選擇器一定要限縮。** 兩個工作台常駐掛載（§7.2），看不見
   的那一半仍在 DOM 裡；而 Playwright 的 `name` 預設是**子字串**比對，所以
   `getByRole('button', { name: '設定' })` 會連收件匣裡「內文剛好提到設定」的
   Mention 卡片一起選中，撞上 strict mode。它是**資料相關的偶發**——換一批
   Mention 就不會發生，看起來像功能壞掉。`tests/e2e/test_ui_redesign.cjs` 開頭
   有兩條規則與現成的 helper（`topBarButton`／`settingsDialog`／
   `SUMMARY_LISTBOX`），照用就好。
6. **規格 §11.1 的快捷鍵表只實作了五分之二**（見上面「這次要做的」第 1 項）。
   它同時卡住 §10.6 的「在宣告文字裡告知快捷鍵」。
