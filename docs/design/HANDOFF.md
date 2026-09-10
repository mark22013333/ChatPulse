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

現況：分支 feature/ui-redesign-evidence-first，12 個 commit，main 未動，
工作區乾淨。規格書的八個 Phase 都實作完成並驗證過，239 項測試全綠。

這次要做的，依序：

1. 修 Bug 1（收件匣按鈕）與 Bug 2（設定頁要按 N 次關閉）。
   兩者的根因、建議修法、驗收條件都寫在 HANDOFF.md 第一節，
   已經定位到 檔案:行號 並有資料佐證，**不要重新調查，直接照著修**。
   但如果你發現那個修法本身有問題，回報、不要照做。

2. 修完之後補上這兩個場景的元件測試（HANDOFF 未完成工項第 4 項）。
   目前 239 項全是純函式測試，元件行為沒有自動化回歸——這兩個 bug
   正好是最值得先蓋住的兩條。

3. 有餘力再往下做未完成工項 1～3（虛擬清單的鍵盤導航、?merge= 的 URL
   同步、兩個回覆設定檔的共用元件）。

工作方式：
- 每一項獨立 commit，Conventional Commits、繁體中文。
- 每個 commit 前跑 typecheck ＋ test，並透過 scripts/webapp.py 重建
  （單獨 npm run build 會讓 dist 被判 stale，指令在 HANDOFF 第四節）。
- 宣告修好之前要有本 session 的實際證據：測試輸出或瀏覽器實測。
  用瀏覽器探針時注意 HANDOFF 第四節列的三個陷阱，特別是
  「回報 0 次必須有正對照」與「DOM 探針要限縮在非 inert 的那個 pane」。
- HANDOFF 第三節那七條約束是踩過坑寫出來的，動到相關程式碼前先看一眼。
````

---

## 30 秒現況

- **分支**：`feature/ui-redesign-evidence-first`，**12 個 commit**（11 個改版 ＋ 這份交接），**`main` 未動**。
- **狀態**：規格書的八個 Phase 全部實作完成並各自驗證過。改版本身可以用。
- **測試**：239 項 / 16 檔全綠。`npm --prefix dashboard/frontend run test`
- **啟動**：`./chatpulse.sh web`。驗證時我用 `--port 8010` 另開一個埠，避免佔用你正在用的 8000。
- **待辦**：下面兩個已定位的 bug，加上五項未完成工作。

```bash
# 接手後先跑這三個確認基準
npm --prefix dashboard/frontend run typecheck        # 應為零錯誤
npm --prefix dashboard/frontend run test             # 應為 239 passed / 16 files
.venv/bin/python -c "import sys; sys.path.insert(0,'scripts'); import webapp; print(webapp.frontend_state())"
# 期望輸出：ready
```

---

## 一、兩個已定位的 bug（根因已確認，附證據）

### Bug 1：從摘要工作台產生的草稿，在待處理分頁卻顯示「退回待處理」

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

### 1. 虛擬清單的 roving tabindex（規格 §10.5）
436 筆 Space 目前仍是**逐個 Tab**，鍵盤使用者要走很久。⌘K 命令面板提供了替代路徑，所以不是死路，但這條該補。

做法（規格 §10.5 有完整說明）：`<ul role="listbox">` ＋ `<li role="option" aria-selected>`，`tabIndex` 只有 active 那列是 0；`↑↓ Home End PageUp PageDown Enter`；移動時先 `virtualizer.scrollToIndex({ align: 'auto' })`（**不要 `center`**）再取焦。按鍵→index 的計算抽到 `lib/listNavigation.ts` 純函式測。

**不要用 `aria-activedescendant`**：它要求被指向的節點常駐 DOM，而虛擬滾動只掛可視範圍 ＋ overscan 12 列。

檔案：`components/SpaceList.tsx`。注意它同時服務單選（摘要）與複選（Reference Space）兩種模式。

### 2. `?merge=` 的 URL 同步
`lib/route.ts` 的 `parseHash` 已經支援並有測試（含順序與去重），但收件匣的勾選還沒寫回網址，所以「勾了兩則要合併」的狀態不能貼連結分享，重新整理也會掉。

做法：`MentionInbox` 的 `toggleMerge` 之後呼叫 `navigate(hashForMentions(primaryId, mergeIds), { replace: true })`；`useRouteSync` 反向套用時需要 store 有 `setMergeIds`（**新增** action，不要改既有的 `toggleMerge`）。

### 3. `QuickReplySettings` 與 `ReplyDefaultsPage` 的共用元件
兩個檔（**365 行**與 **312 行**）渲染**同一組** Select 選項——口氣、Persona、提示詞的 `SelectItem` 內容逐字重複。改一邊忘了另一邊就會不一致。

做法：把三組 `SelectContent` 的內容抽成 `components/settings/replyControls.tsx` 的共用元件。

### 3b. 檔案大小門檻沒達成（規格 P4 的驗收條件，前一個 session 漏了沒檢）
規格 §14 的 P4 有一條「**全域最大檔 < 250 行**」（`lib/types.ts` 是認可的例外），但沒有實際跑過那條檢查就結案了。現況：

```bash
find dashboard/frontend/src -name '*.tsx' -o -name '*.ts' | grep -v test | xargs wc -l | sort -rn | head
```

| 檔案 | 行數 |
| :--- | ---: |
| `lib/types.ts` | 560（純型別，規格認可的例外） |
| `components/DraftReplyWorkspace.tsx` | **477** |
| `App.tsx` | 425 |
| `store/replySettings.ts` | 413（store，本次未動） |
| `components/SummaryWorkspace.tsx` | 371 |
| `components/draft/QuickReplySettings.tsx` | 365 |
| `lib/evidence.ts` | 360 |
| …另有 6 個介於 256–341 之間 | |

**這不是急件**——477 行的 `DraftReplyWorkspace` 已經比改版前的 656 行好很多，也不影響功能。但規格說要拆而沒拆，該記在帳上。真要動的話，規格 §9.2 有完整的拆檔清單（`MentionSourceCard`／`ReferenceSpacePicker`／`CodeRefPicker`／`DraftSetupPanel`／`DraftOutputPane`／`DraftReplyEditor`／`SendReplyConfirm`）。

順序建議：**先修兩個 bug、先補元件測試，再談拆檔**。沒有元件測試的情況下拆 477 行是在沒有安全網的高處走。

### 4. 元件層測試（規格 §15.4）
目前 239 項**全是純函式**。元件行為（送出確認框、Sepia 三態、證據欄、命令面板）是靠瀏覽器實測驗過的，沒有自動化回歸——下一個人改壞了不會有人告訴他。

規格 §15.4 列了四條高價值的測試與工具決策（加 `@testing-library/react` ＋ `jsdom`，用 `test.projects` 讓 `*.test.tsx` 走 jsdom、`*.test.ts` 維持 node，現有 239 項執行環境完全不變）。

**Bug 1 與 Bug 2 修完之後，這兩個場景正好是前兩條元件測試的好題目。**

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

分支尚未推送，`main` 未動。要合併時照專案慣例 `git merge --no-ff`。
