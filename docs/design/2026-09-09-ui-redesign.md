# ChatPulse 介面改版設計規格

> 版本：1.0（2026-09-09）
> 狀態：待實作
> 對象：執行改版的實作 Agent
> 相關：`CONTEXT.md`（統一語言）、`docs/api-contract.md`（API 契約）、`docs/adr/`（既有決策）

---

## 實作進度（隨 Phase 更新）

| Phase | 狀態 | commit | 與規格的落差 |
| :--- | :--- | :--- | :--- |
| 規格書本身 | — | `abb5013` | — |
| P0 設計 token 地基 | 完成 | `bb385d4` | 無。另補了 §3.5 的相容層與 §3.6 的既有 CSS 處置，規格已回填 |
| P1 hash 路由 | 完成 | `757ad07` ＋ `796be65` | **落差已補完**：`?merge=` 的 URL 同步接上了（含反向同步；網址刻意不表達「只勾了一則」，`route.test.ts` 有一條守著這個設計決定） |
| P2 保留掛載 | 完成 | `472f0c7` | 效能量測用「串流中隱藏側內容仍增長」與 API 呼叫計數取代 React DevTools Profiler |
| P3 設定中心 | 完成 | `7d51444` ＋ `14579bf` ＋ `08c0df5` | **落差已補完**：共用下拉抽成 `settings/replyControls.tsx`（365→200、312→139）；`CodeProjectSettings.tsx` 檔名已消失，內容進 `CodeProjectsPage.tsx`（177）與 `CodeProjectForm.tsx`（144），順手修掉兩層各畫一個「參考專案」h2 的缺陷 |
| P4 證據欄 | 完成 | `e3cbed3` ＋ 後續拆檔 | 行數門檻**已達成**，但門檻本身在 2026-09-10 修訂過（§9.1）：原文的「全域最大檔 < 250」只在元件上站得住，改成「**元件與 hook < 250，`store/` 與 `lib/` 另計**」。元件側逐一拆完：`DraftReplyWorkspace` 478→86（`fa268d8`）、`App.tsx` 428→`AppShell`／`TopBar`／`useBootstrap`（`748f94a`）、`SummaryWorkspace` 383→61 ＋ `summary/` 三檔、`MentionInbox` 337→224 ＋ `inbox/` 兩檔、`SpaceMessagePreview` 287→175 ＋ `preview/` 兩檔、`AppShell` 279→234（抽出 `Pane`／`StreamLiveRegions`／`common/SkipLink`）。實測最大元件 243 |
| P5 色彩與排版退役 | 完成 | `02ab318` | 無。守門測試 `lib/tokens.test.ts` 已上，含三條正對照 |
| P7 響應式 | 完成 | `323d691` ＋ `89672fe` | **落差已補完**：768–1024 的主從切換做了，判準直接來自網址（`#/summary` 是清單、`#/summary/:key` 是工作區），收起來的那一半用 §7.2 的手法而不是 `display:none`——後者實測會讓虛擬清單的 `scrollTop` 從 0 跳到 1296 |
| 無障礙與文案 | 完成 | `e7fecdd` ＋ `26b495c` ＋ `dbfdc11` | **落差已補完**：`title=` 29 → 2（只剩兩處 ConfirmDialog 的 title **prop**，不是 DOM 屬性）；§10.6 的串流宣告三層已做，完成宣告也已改成告知真的存在的快捷鍵（⌘⇧C） |
| P6 命令面板與鍵盤 | 完成 | `164ddc1` ＋ `880e4ea` ＋ `b9e55ce` | **落差已補完**：roving tabindex 做了（`880e4ea`）；§11.1 那張表的十個缺鍵也補完了（`b9e55ce`）——`g s`／`g m`／`g ,`／`g h` 兩鍵序列、收件匣清單的 ↑↓、⌘Enter、⌘.、⌘⇧C、`[`／`]`、`?` 說明面板。說明表與解析器之間有漂移守衛 |
| §15.1 `lib/mergeCopy.ts` | 完成 | `HEAD` | 改版收工時漏掉，2026-09-10 盤點才發現：它只寫在 §15.1 正文與 §9.2 目錄結構裡，沒進這張表。已建立；`merge.ts` **不**做 re-export（會與 mergeCopy 形成循環 import），唯一呼叫端改 import |
| §16.3 `src/dev/TokenSheet.tsx` | **未做** | — | 那是**驗證工具**不是產品功能（§16.3 是「怎麼驗」的章節）。token 的正確性最後是由 `lib/tokens.test.ts` 的五條守門規則 ＋ 三條正對照機械化守住的，比一頁人工比對的 sheet 更可靠、也不會過期。要做的話它是獨立的一件事，不阻擋合併 |

### 收工數字（只算 `.tsx`，排除守門測試自己的正對照樣本）

| 指標 | 改版前 | 改版後 |
| :--- | ---: | ---: |
| 繞過 token 的具名色 | 107 | **0** |
| 任意像素字級 | 118 | **0** |
| `title=` 屬性 | 29 | **2**（兩處 ConfirmDialog 的 title **prop**，不是 DOM 屬性；守門測試鎖住） |
| `.metric`（tabular-nums） | 0 | **29** |
| `role=` | 2 | **8** |
| `sr-only` | 2 | **8** |
| 前端測試 | 159 | **517**（node 純函式 ＋ jsdom 元件兩個 project） |
| 瀏覽器 E2E | 0 | **61**（`tests/e2e/test_ui_redesign.cjs`，真 Chromium） |
| 最大的元件檔 | 913（`ReplySettings.tsx`） | **243**（`SpaceList.tsx`） |

> 上表的「改版後」數字更新至 2026-09-10 收尾（第四輪）。`title=`、測試數與
> 最大元件檔三列在收工當下分別是 12／239／477，後面三輪逐一補完。

### 尚未做的（2026-09-10 收尾：只剩一項刻意不做）

原本這一段列了五項。**五項都做完了**，另外還補了改版收工時沒列進來的四塊：

| 原本列的 | 現況 |
| :--- | :--- |
| 1. 虛擬清單的 roving tabindex（§10.5） | 完成 `880e4ea` |
| 2. `?merge=` 的 URL 同步 | 完成 `796be65` |
| 3. `QuickReplySettings` 與 `ReplyDefaultsPage` 的共用元件 | 完成 `14579bf`；同一則帳裡的檔案大小門檻見 §9.1 的 2026-09-10 修訂 |
| 4. 元件層測試（§15.4 的四條） | 完成 `64d6733`／`4db3303`，並在後續每一項都跟著補 |
| 5. `code_terms` 手動指定檢索關鍵字 | 完成 `ff601f3` |

另外補完的三塊：**§10.6 串流的螢幕閱讀器宣告**（`dbfdc11`）、**§11.1 快捷鍵表的
十個缺鍵**（`b9e55ce`）、**§12 的 768–1024 主從切換**（`89672fe`）。

**還剩一項，而且是刻意不做的**：§16.3 的 `src/dev/TokenSheet.tsx`（開發路由
`#/dev/tokens`，把每個 token × 每種狀態 × 兩主題印在一頁）。理由寫在上面那張
進度表——它是驗證工具不是產品功能，而 token 的正確性最後由
`lib/tokens.test.ts` 的守門規則機械化守住了。

**這一段的教訓**：`lib/mergeCopy.ts`（§15.1）漏了三輪才被發現，因為它只寫在
正文裡、沒進進度表。往後的落差請一律寫回上面那張表，**不要只寫在正文、也不要
新開清單**——兩個地方各記一份必然漂移。

**驗證方式的一則教訓**（值得寫進 §16.3）：用瀏覽器探針量「某件事發生了幾次」時，
**「0 次」必須有正對照才可信**。實測時 `fetch` 攔截器一度回報「切頁籤 0 次 API 呼叫」，
但那也可能是攔截器根本沒生效；補上「按強制刷新應該記到 1 次」的正對照之後，那個 0 才有意義。
同理，DOM 探針要記得**限縮在 active 的那一半**——兩個工作台常駐掛載、設定是覆蓋層，
`document.querySelector` 會選到背景那一份。

---

## 0. 這份文件怎麼用

這是一份**可直接照做**的規格。每一節都給到實作層級：token 給實際數值、元件給職責與 props、每個 Phase 給可勾選的驗收條件。

三條使用規則：

1. **§17 的紅線不可違反。** 那些不是建議，是踩過坑寫出來的約束。
2. **Phase 順序不可調換。** §14 說明了哪幾組是相依而非偏好。
3. **文件裡的每個 `檔案:行號` 都是撰寫當下（commit `188e285`）的實際位置。** 改動過程中行號會漂移，以符號名為準；找不到就先確認是不是前一個 Phase 已經改掉了。

**範圍邊界**

| 在範圍內 | 不在範圍內 |
| :--- | :--- |
| `dashboard/frontend/`（唯一的 React 前端） | **後端**——零改動，要用的欄位都已支援 |
| `dashboard/frontend/index.html`（favicon、防閃爍 script） | `docs/index.html`（634 行的獨立手刻文件站，自成一套 CSS 變數，與 dashboard 無共用） |
| `docs/api-contract.md` 的一處文件漂移修正（§13.3） | `mcp_app/`（純 Python，無 UI） |
| — | **新增產品功能**。本次只做設計層與已被埋起來的既有能力 |

---

## 1. 背景與問題

### 1.1 產品決定了介面的工作

ChatPulse 給工程團隊用。Viewer 以自己的 Google 帳號登入，查閱自己加入的 Space（目前 436 個）、產出結構化 Summary、在被人 @ 時取得一份可修改的 Draft Reply。

關鍵事實：**Draft Reply 以 Viewer 本人的身分送出，送出後無法在 ChatPulse 撤回。**

所以介面真正的工作不是「把功能排好」，是**讓人在按下送出之前，看得出這份草稿建立在什麼證據之上**——脈絡取了幾則、涵蓋哪段時間、是否連續、圖片讀進去幾張、程式碼命中哪些檔案、Sepia 潤稿到底有沒有採用、這一送會結掉幾則 Mention。這些都是決策依據，不是裝飾。

功能面已經相當完整。本次改版**不新增產品功能**，只做設計層與已被埋起來的既有能力。

### 1.2 功能性破洞

| 問題 | 位置 | 後果 |
| :--- | :--- | :--- |
| 右欄寫死 `hidden lg:flex` | `App.tsx:201` | 窗寬 <1024px 時，採集器狀態、Token 用量、參考專案設定、摘要歷史**整批消失且無替代入口** |
| 沒有 router | 全域 | 重新整理跳回摘要頁；瀏覽器返回鍵離開 app；無法把「這則 Mention」的連結貼給自己 |
| 頁籤用條件渲染 | `App.tsx:177-220` | 元件真的卸載重掛。`SummaryWorkspace.tsx:97-108` 與 `DraftReplyWorkspace.tsx:113-123` 兩段註解記錄了它造成的災情——掛載時的 `reset()` 把還在串流的內容清光。現在靠「只有真的換 Space／換 Mention 才 reset」繞過，但切頁籤仍會讓 45 秒輪詢與 `codeProjects.load()` 重跑 |
| `codeProjects.load()` 零去重 | `store/codeProjects.ts` | 上一列的直接成因。`CodeProjectSettings.tsx:36-38` 與 `DraftReplyWorkspace.tsx:106-108` 各呼叫一次，兩處都沒有 `loaded` 旗標 |
| 沒有獨立設定頁 | — | `ReplySettings.tsx` 一個檔 **913 行**裝了口氣／Persona CRUD／Preset CRUD／Sepia 四件事，塞在草稿工作區左欄（`DraftReplyWorkspace.tsx:273`）；`CodeProjectSettings.tsx` 塞在右欄 |
| 串流時整棵子樹重繪 | `DraftReplyWorkspace.tsx:76-99` | 用**無 selector 的 `useDraftStore()`**，每個 chunk 都重繪整棵子樹，包含 436 筆的虛擬清單側欄 |

### 1.3 設計層問題（實測基準）

改版前量測，收工時要再量一次做對照。

| 指標 | 現況 | 取得方式 |
| :--- | ---: | :--- |
| 硬寫語意色（sky/emerald/amber/violet/rose/teal） | **107** | `grep -roE '\b(bg\|text\|border\|ring\|from\|to)-(sky\|amber\|emerald\|violet\|rose\|teal)-[0-9]{2,3}' src \| wc -l` |
| 任意像素字級 `text-[…]` | **118** | `text-[10px]` 66、`text-[11px]` 51、`text-[0.8rem]` 1（`ui/button.tsx:25`） |
| `text-xs`（12px） | **62** | grep |
| `title=` | **29** | grep |
| `tabular-nums` / `font-variant-numeric` | **0** | grep（所有數字都不對齊） |
| 死碼 UI primitive | **5** | `badge`／`separator`／`skeleton`／`tooltip`／`scroll-area` 各 0 import |
| `role=` | 2 | grep |
| `sr-only` | 2 | grep |
| `motion-safe:` | 1 | `App.tsx:258` |

**最刺眼的一處**：`DraftReplyWorkspace.tsx:383-506` 把所有證據壓成一排會換行的彩色小藥丸——脈絡則數、合併幾則、圖片幾張、供應商、口氣、Persona、自訂提示、Sepia 狀態、每個 Reference Space，全部 11px、四色混雜、位置隨內容浮動。最該一眼看清的資訊，變成最難掃的一塊。

### 1.4 後端已支援但前端沒用

| 能力 | 後端位置 | 現況 |
| :--- | :--- | :--- |
| 釘選 Space | `core/db.py:50` 欄位、`server.py:701` PATCH、`server.py:1478` 回傳 `pinned` | UI 完全沒有。`store/spaces.ts:83-89` 的 `filterSpaces` **只過濾、零排序** |
| Persona 淨化前後對照 | `GET /personas/{id}?include_raw` | `api.ts:174` 有包裝，UI 沒有入口 |
| `code_terms` 手動指定檢索關鍵字 | `server.py:1984-1986` | `store/draft.ts:248-251` 只送 `code_refs`，沒有輸入框 |
| 參考專案建檔前 dry-run 驗證 | `POST /code-projects/verify` | 未被呼叫，只能先存再驗 |
| 診斷資訊 | `GET /health` | 無任何畫面 |
| 用量天數 1–90 | `GET /usage?days=` | `UsagePanel.tsx:15` 寫死 14 |
| 摘要歷史 1–1000 | `GET /summaries?limit=` | `store/summary.ts:73` 寫死 50 |
| 草稿預設參考專案／環境 | `default_code_project_id` / `default_code_environment`（`server.py:706-707`） | `api.updatePreferences` 沒帶這兩欄 |

**其中一項本次刻意不做**：`code_terms` 手動指定檢索關鍵字。它要在 `store/draft.ts` 的 `generate()` 請求裡多送一個欄位、還要多一個 state 與 setter，而 `draft.test.ts` 有 27 項打在這個 store 上。雖然「加欄位」在 §17 紅線 3 的字面上是允許的，但 `generate()` 的請求組裝是既有測試最密集覆蓋的一段，改它的收益（一個次要輸入框）與風險不成比例。**列為改版完成後的獨立後續項目**，屆時它自帶 store 測試更新。

---

## 2. 設計方向與原則

### 2.1 方向：證據優先工作台

把「這份草稿建立在什麼之上」從角落的灰色小字，升格為版面的主結構。右欄從雜物抽屜改成**固定的證據欄**，設定類內容全部抽到獨立設定中心。

### 2.2 設計原則（衝突時依此裁決）

1. **任何無法回溯到 SSE `meta` 某個欄位的東西，不准進證據欄。** 這條直接排除裝飾的可能性。
2. **顏色是稀有資源。** 全站只有一個訊號色加一個錯誤色。Viewer 一天掠過幾十則草稿，第 15 個彩色 badge 等於沒有 badge。
3. **狀態先用線型與字符，顏色只是冗餘。** solid／dashed／dotted 對應完整／降級／缺值——「脈絡不連續」本來就該畫成一條斷掉的線，這不是為了無障礙才補的補丁，是語意直譯。
4. **全站最大的字是一個數字。** 字級上限 22px 且只給計量值，章節標題一律小於它。這個工具的主角是「42 則」「a1b3c9d」，不是「脈絡分析」四個字。
5. **等寬字只服務對齊。** 數字、commit sha、branch、路徑、時間用 mono；中文、人名、供應商名、草稿內文一律不用——CJK 在 mono 下會落回比例字體，等寬根本沒生效，卻讓文字讀起來像 log。
6. **`title=` 不是一種呈現方式。** 決策必需的資訊一律改成畫面上讀得到的文字。tooltip 只能承載與可見文字重複的內容，並由自動化測試機械性擋住（§15）。
7. **大膽只用在一個地方。** 記憶點是證據欄，其餘一律安靜：不加漸層、不給每張卡片同一種灰陰影、不用 all-caps 小標籤、不在按鈕文字後面加箭頭、**全案禁用中點 `·` 串接 meta**。

### 2.3 一個順手修正的識別不一致

`index.html:8` 的 favicon 是一條**脈搏線**（ECG 折線，`M4 12h3l2-6 4 12 2-6h5`），與產品名 ChatPulse 對得上。但 app 內的 logo 是 `ZapIcon` 閃電（`App.tsx:137`、`LoginScreen.tsx:25`），兩者毫無關係。

改版統一採用脈搏線作為識別：把該路徑抽成 `src/app/PulseMark.tsx`（一個 inline SVG 元件，`currentColor` 描邊；位置見 §9.2），頂列與登入畫面共用，並把 favicon 裡硬編碼的 `%230ea5e9` 換成新的訊號色 `%230068a5`。

---

## 3. 設計 token

### 3.1 對比比值的取得方式

下表所有比值都是用 OKLCH → 線性 sRGB → WCAG 2.x 相對亮度算出來的，計算程式先用已知答案自我驗證過（純白/純黑 = 21.00、`oklch(1 0 0)` → `#ffffff`、Tailwind sky-500 的 oklch 值 → `#0ea5e9` 精確還原）。

**但這是計算值，不是量測值。** 實作時仍要用 Chrome DevTools 的對比檢查器對實際渲染結果逐格複驗，尤其是半透明疊色（`--signal-wash` 等）——那些的最終對比取決於底下疊了什麼。

### 3.2 兩種規線是兩件事（重要）

| token | 用途 | 對比要求 | 實際值 |
| :--- | :--- | :--- | :--- |
| `--line` / `--line-strong` | **純裝飾**的分隔線、卡片邊框 | 不受 WCAG 1.4.11 管 | 淺 1.27 / 1.67，深 1.35 / 1.85 |
| `--line-evidence` | **承載語意**的規線（證據欄的 solid/dashed/dotted 代表完整/降級/缺值） | **≥3:1**（1.4.11 非文字對比） | **淺 3.49:1，深 3.35:1** |

把它們混用就等於讓無障礙要求毀掉細線的美感，或讓語意線細到看不出線型。分開之後兩邊都成立。

### 3.3 完整 token（可直接貼進 `src/index.css`）

```css
@import "tailwindcss";
@import "tw-animate-css";
@import "shadcn/tailwind.css";
@import "@fontsource-variable/geist";
@import "@fontsource-variable/geist-mono";   /* 本次唯一新增的執行期相依 */

@custom-variant dark (&:is(.dark *));

@theme inline {
  /* ── 字體 ────────────────────────────────────────────────
     Geist 的 subset 只有 latin/latin-ext/cyrillic/vietnamese，
     沒有 CJK。在此之前 --font-sans 完全沒有 CJK fallback 鏈，
     介面上每一個中文字都是瀏覽器自己挑的——這是既有的隱性 bug，
     跨 macOS／Windows 尤其明顯（本專案兩個平台都支援）。 */
  --font-sans: 'Geist Variable', 'PingFang TC', 'Hiragino Sans CNS',
               'Noto Sans TC', 'Microsoft JhengHei UI', 'Microsoft JhengHei',
               'Source Han Sans TC', system-ui, sans-serif;
  --font-mono: 'Geist Mono Variable', ui-monospace, 'SF Mono', 'Cascadia Mono',
               'Roboto Mono', Consolas, 'PingFang TC', 'Microsoft JhengHei UI',
               monospace;
  --font-heading: var(--font-sans);

  /* ── 字級：12px 絕對下限；13px 是「用來決定能不能送」的下限 ── */
  --text-2xs: 0.75rem;        --text-2xs--line-height: 1.4;    /* 12 單位、快捷鍵提示 */
  --text-xs: 0.8125rem;       --text-xs--line-height: 1.5;     /* 13 標籤、meta */
  --text-sm: 0.875rem;        --text-sm--line-height: 1.6;     /* 14 介面預設 */
  --text-base: 0.9375rem;     --text-base--line-height: 1.75;  /* 15 讀物內文 */
  --text-md: 1.0625rem;       --text-md--line-height: 1.4;     /* 17 區塊標題 */
  --text-lg: 1.3125rem;       --text-lg--line-height: 1.3;     /* 21 工作台標題 */
  --text-metric: 0.9375rem;   --text-metric--line-height: 1.2; /* 15 一般計量 */
  --text-metric-lg: 1.375rem; --text-metric-lg--line-height: 1.1; /* 22 全站上限 */

  --leading-tight: 1.3;
  --leading-ui: 1.5;
  --leading-read: 1.75;
  --tracking-tight: -0.006em;

  /* ── 間距（Tailwind v4 的 --spacing 命名空間）────────────── */
  --spacing: 0.25rem;
  --spacing-gutter: 1rem;            /* 面板內縮，全站只有這一個值 */
  --spacing-gutter-tight: 0.625rem;
  --spacing-stack: 0.75rem;          /* 區塊間垂直節奏 */
  --spacing-row: 1.75rem;            /* 證據列最小高 28px */
  --spacing-tap: 2rem;               /* 互動元件最小 32px */

  --container-rail: 20rem;           /* 證據欄 320px */
  --container-inbox: 18rem;          /* 左欄 288px */
  --container-metric: 4.5rem;        /* 數字欄固定寬，全欄共用同一個右緣 */

  /* ── 圓角：顯式階梯，不再用單一 --radius 乘倍數 ────────── */
  --radius-xs: 2px;  --radius-sm: 3px;  --radius-md: 4px;
  --radius-lg: 6px;  --radius-xl: 8px;  --radius-2xl: 10px;
  --radius-3xl: 12px; --radius-4xl: 12px;   /* 收掉 pill：badge 改用 rounded-sm */

  /* ── 陰影：內容一律不投影；只有真的浮起來的東西有 ────────
     ⚠ 必須寫成 var() 指到 :root/.dark 的原始變數。
     `@theme inline` 會把值**內聯**進 utility，若在這裡直接寫字面值，
     `.dark` 的覆寫就永遠不會生效（深色會拿到淺色的陰影）。 */
  --shadow-flat: none;
  --shadow-raised: var(--elevation-raised);
  --shadow-overlay: var(--elevation-overlay);

  /* ── 動效：只有三檔 ─────────────────────────────────────── */
  --ease-out: cubic-bezier(0.2, 0, 0, 1);
  --motion-fast: 90ms;
  --motion-base: 150ms;
  --motion-slow: 240ms;

  /* ── 語意色 → Tailwind utility ──────────────────────────── */
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-surface: var(--surface);
  --color-raised: var(--raised);
  --color-fg-dim: var(--fg-dim);
  --color-fg-subtle: var(--fg-subtle);
  --color-line: var(--line);
  --color-line-strong: var(--line-strong);
  --color-line-evidence: var(--line-evidence);
  --color-signal: var(--signal);
  --color-signal-on: var(--signal-on);
  --color-signal-wash: var(--signal-wash);
  --color-signal-line: var(--signal-line);
  --color-verified: var(--verified);
  --color-caution: var(--caution);
  --color-caution-line: var(--caution-line);
  --color-provenance: var(--provenance);
  --color-destructive: var(--destructive);
  --color-disabled-fg: var(--disabled-fg);

  /* shadcn 相容別名：不改 ui/*.tsx 也能吃到新主題 */
  --color-card: var(--surface);          --color-card-foreground: var(--foreground);
  --color-popover: var(--raised);        --color-popover-foreground: var(--foreground);
  --color-primary: var(--foreground);    --color-primary-foreground: var(--background);
  --color-secondary: var(--muted);       --color-secondary-foreground: var(--foreground);
  --color-accent: var(--muted);          --color-accent-foreground: var(--foreground);
  --color-muted: var(--muted);           --color-muted-foreground: var(--fg-dim);
  --color-border: var(--line);           --color-input: var(--line-strong);
  --color-ring: var(--signal);
}

/* ── 淺色：冷調紙，不是暖奶油 ──────────────────────────────── */
:root {
  color-scheme: light;
  --background:    oklch(0.985 0.002 250);
  --surface:       oklch(0.998 0.001 250);  /* 內容面幾乎與底同高，靠規線分群 */
  --raised:        oklch(1     0     0  );  /* 浮層在紙上比紙更白 */
  --foreground:    oklch(0.22  0.006 250);  /* #191b1d  16.58:1 */
  --fg-dim:        oklch(0.48  0.006 250);  /* #5b5e61   6.26:1 */
  --fg-subtle:     oklch(0.55  0.006 250);  /* #6f7275   4.65:1（標籤下限） */
  --muted:         oklch(0.965 0.003 250);
  --line:          oklch(0.905 0.004 250);  /* #dee0e2   1.27:1  純裝飾 */
  --line-strong:   oklch(0.82  0.004 250);  /* #c2c4c7   1.67:1  純裝飾 */
  --line-evidence: oklch(0.62  0.008 250);  /* #83878b   3.49:1  承載語意 */

  --signal:        oklch(0.50  0.126 245);  /* #0068a5   5.70:1  色域內 */
  --signal-on:     oklch(0.99  0     0  );  /* 白字在 signal 上 5.78:1 */
  --signal-wash:   oklch(0.50  0.126 245 / 0.08);
  --signal-line:   oklch(0.50  0.126 245 / 0.32);
  --verified:      var(--signal);           /* 同色，靠 ✓ 字符區分 */

  --caution:       oklch(0.47  0.055 75);   /* #6d5637   6.59:1  曬過的石墨，不是琥珀 */
  --caution-line:  oklch(0.47  0.055 75 / 0.45);

  --provenance:    var(--fg-subtle);        /* 刻意無彩度：供應商是署名不是狀態 */
  --destructive:   oklch(0.505 0.175 24);   /* #b3282f   6.17:1 */
  --disabled-fg:   oklch(0.66  0.004 250);  /* #909295   2.98:1  取代 opacity-50 */

  /* 陰影的原始值（`@theme inline` 只放 var 指標，見上方警告） */
  --elevation-raised:  0 0 0 1px var(--line-strong),
                       0 1px 1px -0.5px oklch(0.22 0.006 250 / 0.08),
                       0 6px 16px -6px oklch(0.22 0.006 250 / 0.18);
  --elevation-overlay: 0 0 0 1px var(--line-strong),
                       0 2px 3px -1.5px oklch(0.22 0.006 250 / 0.10),
                       0 14px 34px -12px oklch(0.22 0.006 250 / 0.24);

  /* ── shadcn 原始變數相容層（必要，見 §3.5）──────────────── */
  --border: var(--line);
  --popover: var(--raised);
  --popover-foreground: var(--foreground);
  --secondary: var(--muted);
  --muted-foreground: var(--fg-dim);
  --radius: 6px;
}

/* ── 深色：帶冷的石墨，不是近黑 ────────────────────────────── */
.dark {
  color-scheme: dark;
  --background:    oklch(0.190 0.004 250);
  --surface:       oklch(0.225 0.004 250);
  --raised:        oklch(0.260 0.005 250);
  --foreground:    oklch(0.955 0.003 250);  /* #eff0f2  16.20:1 */
  --fg-dim:        oklch(0.735 0.004 250);  /* #a7a9ac   7.86:1 */
  --fg-subtle:     oklch(0.615 0.004 250);  /* #838587   4.97:1 */
  --muted:         oklch(0.255 0.004 250);
  /* 不透明而非 alpha：alpha 邊框在多層面上會互相疊加變深 */
  --line:          oklch(0.300 0.005 250);  /* #2c2e30   1.35:1  純裝飾 */
  --line-strong:   oklch(0.380 0.005 250);  /* #404345   1.85:1  純裝飾 */
  --line-evidence: oklch(0.520 0.008 250);  /* #66696d   3.35:1  承載語意 */

  --signal:        oklch(0.74  0.144 245);  /* #50b3ff   8.09:1  色域內 */
  --signal-on:     oklch(0.17  0.004 250);  /* 深字在 signal 上 8.38:1 */
  --signal-wash:   oklch(0.74  0.144 245 / 0.10);
  --signal-line:   oklch(0.74  0.144 245 / 0.34);
  --verified:      var(--signal);

  --caution:       oklch(0.780 0.075 78);   /* #d2b281   9.16:1 */
  --caution-line:  oklch(0.780 0.075 78 / 0.45);

  --provenance:    var(--fg-subtle);
  --destructive:   oklch(0.680 0.170 22);   /* #ef6567   5.92:1 */
  --disabled-fg:   oklch(0.480 0.004 250);  /* #5c5e60   2.83:1 */

  /* shadcn 原始變數相容層。在 .dark 也宣告一次，而不是只靠 :root 繼承——
     目前 .dark 與 :root 落在同一個 <html> 元素上，只宣告一次也會正確解析，
     但將來若有人把 .dark 移到某個 wrapper div，只宣告一次就會壞。 */
  --border: var(--line);
  --popover: var(--raised);
  --popover-foreground: var(--foreground);
  --secondary: var(--muted);
  --muted-foreground: var(--fg-dim);
  --radius: 6px;

  /* 暗色沒有光源：浮起＝環線 + 頂緣內高光，不是模糊灰影。
     覆寫的是 --elevation-*（原始值），不是 --shadow-*（那是 @theme 的指標） */
  --elevation-raised:  0 0 0 1px oklch(0 0 0 / 0.55),
                       inset 0 1px 0 0 oklch(1 0 0 / 0.06);
  --elevation-overlay: 0 0 0 1px oklch(0 0 0 / 0.65),
                       inset 0 1px 0 0 oklch(1 0 0 / 0.07),
                       0 18px 40px -14px oklch(0 0 0 / 0.7);
}

/* ── 基線 ──────────────────────────────────────────────────── */
@layer base {
  * { @apply border-line; }
  html { @apply font-sans; -webkit-text-size-adjust: 100%; }
  body { @apply bg-background text-foreground text-sm; font-optical-sizing: auto; }

  /* 計量：全站數字用這個 class，不要用 font-mono 亂灑（原則 5） */
  .metric {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    font-feature-settings: "tnum" 1, "zero" 1;
    letter-spacing: 0;
  }

  /* focus 用 outline 不用 ring——badge.tsx 有 overflow-hidden 會裁掉 ring，
     這是現存的 bug */
  :where(a, button, input, select, textarea, summary, [tabindex]):focus-visible {
    outline: 2px solid var(--signal);
    outline-offset: 2px;
    box-shadow: 0 0 0 4px color-mix(in oklch, var(--signal) 22%, transparent);
    border-radius: var(--radius-md);
  }

  :where(:disabled, [aria-disabled="true"]) {
    color: var(--disabled-fg);
    opacity: 1;   /* 不用 opacity 降對比 */
  }
}

/* ── 減少動態效果：統一處理，不靠逐處 motion-safe: ──────────── */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 1ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 1ms !important;
    scroll-behavior: auto !important;
  }
  /* 光凍住不夠：22 處 animate-spin 是「生成中」的唯一表徵，
     凍住的轉圈等於謊報。三個活體指示器各自定義靜態表徵。 */
  .typing-cursor::after { content: '▋'; animation: none; }
  .live-dot { animation: none; box-shadow: 0 0 0 2px var(--signal-line); }
  .live-mark::after { content: '生成中'; font-size: var(--text-2xs); }
}
```

> **Tailwind v4 實測（2026-09-10，tailwindcss 4.3.3）**：`--container-*` 會生成 `w-*` 與 `max-w-*`，`--spacing-*` 會生成 `p-*`／`gap-*`／`min-h-*`。實測 `w-rail`、`max-w-inbox`、`p-gutter`、`gap-stack`、`min-h-tap` 五個 utility 都正常輸出。
>
> 注意 v4 會**樹搖掉沒被使用的 theme 變數**——建置產物裡找不到 `--container-rail` 不代表壞了，只代表還沒有人用它。

### 3.4 遷移手法：`@theme` 別名 shim

**這是整個色彩遷移的關鍵，先做這一步再動任何 `.tsx`。**

在 `@theme inline` 裡把 Tailwind 具名色重新指向語意 token：

```css
@theme inline {
  /* ⚠ 這是暫時的遷移橋樑。P5 完成後必須整段刪除。 */
  --color-sky-300: var(--signal);     --color-sky-400: var(--signal);
  --color-sky-500: var(--signal);     --color-sky-600: var(--signal);
  --color-sky-700: var(--signal);
  --color-emerald-400: var(--verified); --color-emerald-500: var(--verified);
  --color-emerald-600: var(--verified);
  --color-amber-400: var(--caution);  --color-amber-500: var(--caution);
  --color-amber-600: var(--caution);  --color-amber-700: var(--caution);
  --color-violet-400: var(--provenance); --color-violet-500: var(--provenance);
  --color-violet-600: var(--provenance);
  --color-rose-500: var(--line-strong);  --color-teal-500: var(--line-strong);
}
```

它同時解掉 `dark:` 成對寫法的陷阱：`text-sky-500` 與 `dark:text-sky-400` 現在解析到同一個 `var(--signal)`，而 `--signal` 本身已經是主題感知的，所以 `dark:` 變體自動退化成無害的 no-op。**不需要做任何字串取代，107 處就先換上新配色。**

**風險**：它會讓程式碼「看起來已經改好了」。P5 的守門測試（§15.3）就是用來逼出退役的——測試綠了才准刪別名，別名刪了才算 P5 完成。

### 3.5 直接讀原始 CSS 變數的地方（不處理會靜默壞掉）

Tailwind 的 `@theme` 只產生 utility class。有幾處程式碼**繞過 utility 直接讀原始變數**，token 表若沒保留它們，畫面會安靜地壞掉而不報錯：

| 位置 | 讀了什麼 |
| :--- | :--- |
| `ui/sonner.tsx:30-34` | `var(--popover)`、`var(--popover-foreground)`、`var(--border)`、`var(--radius)` |
| `.tsx` 其他處 | `var(--radius-md)`（5 處）、`var(--secondary)`、`var(--foreground)` |
| `index.css` 的 `.markdown-body` 與 `.typing-cursor` | `var(--foreground)`、`var(--muted)`、`var(--muted-foreground)`、`var(--border)`、`var(--color-sky-500, #0ea5e9)` |

§3.3 的「shadcn 原始變數相容層」就是為此而設，**不可刪**。`--radius-md` 由 `@theme` 產生，`--foreground` 與 `--muted` 本來就在，其餘六個靠相容層。

**`.markdown-body` 要一併改**：`h3` 顏色、連結顏色、行內 `code` 顏色現在都是 `var(--color-sky-500, #0ea5e9)`（`index.css:182, 206, 212, 257` 四處），改成 `var(--signal)`，**不保留 hex fallback**——保留 fallback 等於允許一個永遠不會被更新的舊藍色偷偷存在。

**可以安全刪掉的**：`--sidebar-*`（8 個）與 `--chart-1~5`（5 個）在 `.tsx` 裡**零使用**，只存在於 `index.css` 自己。本次一併移除，不要照抄回新的 token 表。

### 3.6 既有 `index.css` 其餘段落怎麼處理

§3.3 取代的是 `index.css:1-130`（import、`@theme`、`:root`、`.dark`、`@layer base`）。**`:132-269` 的其餘段落要逐段處理，不是整檔覆蓋**：

| 現有段落 | 處理 |
| :--- | :--- |
| 細捲軸（`:135-150`） | **保留**。thumb 的 `color-mix(… var(--muted-foreground) …)` 改成 `var(--fg-subtle)`，語意更準（那是「次要前景」不是「靜音文字」） |
| `.markdown-body`（`:153-251`） | **保留結構**，三處改動：① 內文 `font-size: 0.8125rem` → `var(--text-base)`（15px）與 `line-height: var(--leading-read)`；② `h3`／連結／行內 `code` 的 `var(--color-sky-500, #0ea5e9)`（`:182, 206, 212`）→ `var(--signal)`，**不留 hex fallback**；③ `code` 與 `table` 的 `0.78rem` → `var(--text-xs)` |
| `.typing-cursor`（`:253-260`）＋ `@keyframes chatpulse-blink`（`:261-269`） | **保留**。`:257` 的 `var(--color-sky-500, #0ea5e9)` → `var(--signal)`。注意 §3.3 的 reduced-motion 區塊也提到 `.typing-cursor::after`，那是**降級覆寫**，與這裡的常態定義並存不衝突 |
| `.live-dot` / `.live-mark` | **新增**（**已完成**：常態定義在 `index.css:234` 與 `:266`，降級覆寫在 `:293` 起）。§3.3 的 reduced-motion 區塊當時只寫了降級態：`.live-dot` 是頂列頁籤的脈動點（沿用現在 `App.tsx:252-260` 的雙層 ping 結構，抽成 class），`.live-mark` 是證據欄的 `⟳`（`animation: spin var(--motion-slow) linear infinite` 之類）。**兩者都要先有常態，降級覆寫才有東西可覆寫** |

---

## 4. 排版與字體

### 4.1 字體決策

**不換 Latin 字體，保留 Geist；新增 Geist Mono；補上 CJK fallback 鏈。**

理由：這個介面約七成字符是中文，無論如何都由系統字體渲染；`dist/` 進版控，為了換一個只影響三成字符的 Latin face 而灌進 1.4MB 字體檔並不划算。識別的預算改花在脈搏標記與證據帳本上。

真正要修的是既有的隱性 bug：`--font-sans` 沒有任何 CJK fallback 鏈，中文字全由瀏覽器亂挑，跨 macOS／Windows 表現不一致（本專案兩個平台都支援）。§3.3 的 fallback 鏈解掉這件事。

新增相依（已驗證存在）：`@fontsource-variable/geist-mono@5.3.0`。

> 若日後要更強的識別轉向，`@fontsource-variable/ibm-plex-sans`（5.3.0）＋ `@fontsource/ibm-plex-mono`（5.3.0）是同家族 Sans/Mono 的候選。本次不做。

### 4.2 等寬字用在哪、不用在哪

| 用 mono（`.metric` 或 `font-mono`） | 不用 mono |
| :--- | :--- |
| 所有數字與計量、commit sha、branch 名、檔案路徑、Space id、時間戳 | 中文、人名、Space 顯示名、供應商顯示名、**草稿內文與建議回話** |

**必須修掉的現況**：`DraftReplyWorkspace.tsx:606` 的建議回話 `<Textarea>` 是 `font-mono text-xs`。那是要給人讀的中文散文，mono 下中文落回比例字體、等寬完全沒生效，卻讓它讀起來像 log。改成 `text-base`（15px／行高 1.75）。

同理 `contextLabel`（`DraftReplyWorkspace.tsx:44-49`）現在整串套 `font-mono`，改成只有數字部分套 `.metric`。

### 4.3 字級指派規則

| token | px | 用在 |
| :--- | ---: | :--- |
| `text-2xs` | 12 | 單位（則／張／個）、快捷鍵提示、腳註。**絕對下限** |
| `text-xs` | 13 | 欄位標籤、meta 說明。**攜帶決策資訊的文字不得小於這一級** |
| `text-sm` | 14 | 介面預設（按鈕、清單項、輸入框） |
| `text-base` | 15 | 讀物內文（Markdown、建議回話、Mention 原文） |
| `text-md` | 17 | 區塊標題 |
| `text-lg` | 21 | 工作台標題 |
| `text-metric-lg` | 22 | 計量值。**全站字級上限** |

目前 179 處 ≤12px 的用法全部要重新指派。判準：**這行字有沒有參與「能不能送」的判斷**——有就 ≥13px。具體必須升級的：`MERGE_BLOCK_LABEL`（不可合併的原因）、Sepia 未採用的原因、`code_refs` 的命中檔案清單。

---

## 5. 證據欄規格

### 5.1 資料來源

全部已經齊備，來自 `SseMeta`（`src/lib/types.ts:441`）與 `done` 事件的 `DraftPolishMeta`。證據欄**不得顯示任何無法回溯到下表某個欄位的東西**（原則 1）。

| 證據列 | 來源欄位 |
| :--- | :--- |
| 脈絡 | `context.mode` / `.message_count` / `.coverage` / `.time_range` / `.blocks` |
| 回覆對象 | `answering[]`（`mention_id` / `sender_display` / `create_time`） |
| 附件 | `image_count` / `images_skipped[]` |
| 參考 Space | `reference_spaces[]`（`space_name` / `message_count`） |
| 參考專案 | `code_refs[]`（`project_name` / `environment_label` / `branch` / `commit_sha` / `commit_date` / `terms` / `hit_count` / `files` / `truncated` / `notes`）、`code_skipped[]` |
| 生成 | `provider` / `model` |
| 回話設定 | `reply.tone_label` / `.persona_name` / `.custom_prompt` |
| 潤稿 | `polish.polished` / `.fallback_reason` |
| （摘要用）來源 | `space` / `message_count` / `style` |

### 5.2 版式：一本帳本

標籤左、值右，數字欄固定寬（`--container-metric`）讓**所有列的數字共用同一個右緣**；單位（則／張／個）放在數字欄**外側**，用 `text-2xs` 與 `--fg-subtle`。數字自己形成一條乾淨的縱列——這是整套設計最主要的識別特徵。

```
┌ 證據                                    ⟳ ┐   ⟳ 只在生成中出現
│ 脈絡                    討論串      42 則  │
│ ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌ │ ← dashed = coverage:'partial'
│   09-02 14:10 – 09-07 11:02              │
│   原串 38    鄰近 4                       │
│   † 系統沒能取回這則訊息周圍的完整對話，   │
│     脈絡不保證連續                        │
│ 回覆對象                            3 則  │
│ ──────────────────────────────────────── │
│   王小明               09-07 09:12       │  ← critical，常駐不收合
│   李美華               09-07 10:03       │
│   陳大文               09-07 10:41       │
│ 附件                                2 張  │
│ ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌ │
│   † 略過 1 張（格式不支援）               │
│ 參考 Space                          2 個  │
│ ──────────────────────────────────────── │
│   後端維運                        120 則  │
│   SRE 值班                         80 則  │
│ 參考專案                            1 個  │
│ ──────────────────────────────────────── │
│   chatpulse-api                    正式   │
│   main@a1b3c9d                 2026-08-30│
│   命中                              3 檔  │
│   ▸ core/draft_context.py …              │
│ 生成                                      │
│ ──────────────────────────────────────── │
│   Google Gemini                          │
│   gemini-2.5-pro                         │
│ 回話設定                                  │
│ ──────────────────────────────────────── │
│   口氣                      工程師協作     │
│   Persona                     本人風格     │
│ 潤稿                        ✓ Sepia 已核對 │
│ ════════════════════════════════════════ │
│ 證據 8 項                     降級 1 項   │
└───────────────────────────────────────── ┘
```

**欄位順序固定，判準是「最可能讓我不送」優先**：脈絡 → 回覆對象 → 附件 → 參考 Space → 參考專案 → 生成 → 回話設定 → 潤稿。

**缺值顯示 `—`，不抽掉那一列。** 「沒有圖片」與「還沒讀到圖片」是兩件不同的事，位置固定才分得出來。**標籤永不變淡**——缺值時把標籤調暗等於讓欄位消失。

### 5.3 四種狀態

| status | 規線（`border-bottom-style`，色用 `--line-evidence`） | 字符 | 值的顏色 | 附加 |
| :--- | :--- | :--- | :--- | :--- |
| `ok` | solid | — | `--foreground` | — |
| `ok` ＋ 已核對 | solid | `✓` 前置 | `--foreground`，字符用 `--verified` | — |
| `degraded` | **dashed** | `†` 後置 | `--foreground`，字符用 `--caution` | **必附一行說明**，用 `aria-describedby` 綁到該列 |
| `missing` | **dotted** | — | `—`，`--fg-subtle` | — |
| `pending`（生成中） | solid | — | 閃動細豎條 | 該列 `aria-busy="true"` |

solid／dashed／dotted 三種線型在 1px、灰階、色盲條件下都分得出來，而且「不連續的脈絡畫成斷線」是語意直譯。**顏色一律只是冗餘**（WCAG 1.4.1）。

`missing` 絕不可畫成 `ok`——它代表後端沒回報這個欄位（舊版後端），與「值為 0」是兩回事。

### 5.4 生成中 vs 完成後

`meta` 事件在模型開口**之前**就送到前端（這個原則已寫在 `DraftReplyWorkspace.tsx:524-528` 的註解裡），所以證據欄第一秒就存在：

- **有值的列立刻實體渲染**，等值的列顯示標籤 ＋ 閃動細豎條。
- **不做進場動畫。欄位絕不淡入或 stagger——會淡入的數字是還不能相信的數字。**
- 生成中：容器 `aria-busy="true"`，標題列出現 `⟳`（`.live-mark`）。
- 完成後：移除 `⟳`，底部出現帳本合計（`證據 N 項` / `降級 N 項`），`aria-busy="false"`。
- 若 `degraded` 數 > 0，「送出回話」按鈕上方出現一行帶 `†` 的提示指回那幾列。**只標不擋**——判斷權在 Viewer 手上。

### 5.5 展開細節：用 `<details>`，不用 tooltip

有細節的列加原生 `<details>/<summary>`——鍵盤與螢幕閱讀器免費支援。

**不得使用 `ui/tooltip.tsx`**：它是 base-ui Tooltip，觸控裝置一樣摸不到（本次要支援平板），等於把問題從 A 搬到 B。

`critical: true` 的列**不收合**，永遠展開：
- `answering`（送出會一次結掉這幾則，且不可撤回）
- `context` 且 `coverage === 'partial'`

### 5.6 統一型別

新檔 `src/lib/evidence.ts`。純函式、node 環境可測、**不 import 任何 store**。

```ts
import type { SseMeta, DraftPolishMeta } from '@/lib/types'

export type EvidenceKind =
  | 'context'        // 送進模型的脈絡形狀
  | 'answering'      // 這份草稿會回掉哪幾則 Mention
  | 'reference'      // Reference Space
  | 'code'           // 參考專案檢索結果
  | 'images'         // 圖片附件
  | 'model'          // 供應商與模型
  | 'reply-setting'  // 口氣／Persona／自訂提示
  | 'polish'         // Sepia 潤稿結果
  | 'source'         // 摘要的來源 Space 與則數

/**
 * - ok       ：有值且完整
 * - degraded ：有值但不完整（coverage=partial、命中 0 筆、潤稿未採用、圖片被略過）
 * - pending  ：還在跑（串流中，或串流結束後 Sepia 還在潤）
 * - missing  ：後端沒回報這個欄位（舊版後端）。
 *              **不可畫成 ok**——「沒有圖片」與「不知道有沒有讀圖片」是兩件事。
 */
export type EvidenceStatus = 'ok' | 'degraded' | 'pending' | 'missing'

/** 一行明細。取代 title=，一律畫成可見文字。 */
export interface EvidenceDetail {
  label: string
  value?: string
  /** 多值：被略過的圖片、合併回覆的對象、命中的檔案 */
  values?: string[]
  /** 用 .metric 呈現（sha、branch、數字、時間） */
  metric?: boolean
}

export interface EvidenceItem {
  /** 同一次生成內唯一；重新生成整批換掉 */
  id: string
  kind: EvidenceKind
  status: EvidenceStatus
  /** 收合時那一行。不得是唯一資訊來源 */
  summary: string
  /** 右側計量值與單位；沒有計量的列留空 */
  metric?: { value: string; unit?: string }
  detail: EvidenceDetail[]
  /** status !== 'ok' 時的「為什麼」，畫在明細最上面 */
  reason?: string
  /** 例：分支不存在 → { label: '檢查分支設定', href: '#/settings/code-projects/3' } */
  action?: { label: string; href: string }
  /** true 則不得收合 */
  critical?: boolean
}

export interface EvidenceBundle {
  runId: string
  origin: 'summary' | 'draft'
  items: EvidenceItem[]
  /** 非 ok 的項數；抽屜關著時頂列亮點看這個 */
  attentionCount: number
}

export function toEvidence(input: {
  origin: 'summary' | 'draft'
  meta: SseMeta | null
  polish: DraftPolishMeta | null
  streaming: boolean
  /** 注入，evidence.ts 不 import store */
  providerLabel: (name: string) => string
}): EvidenceBundle
```

**缺值處理規則**
- `meta === null` → 只回一個 `kind:'source'`、`status:'pending'` 的項。
- `meta.image_count === undefined` → `status:'missing'`，summary 寫「這個版本的伺服器沒有回報圖片張數」。
- `meta.context === undefined` → 沿用 `DraftReplyWorkspace.tsx:44-49` 現有的 fallback 文字，但標 `missing`，不要偽裝成 `ok`。
- `code_refs[i].hit_count === 0` → `status:'degraded'`，reason 是「這個分支沒有找到相符的程式碼」。
- `polish.polished === false` → `status:'degraded'`，reason 是 `fallback_reason`。

### 5.7 四處 `title=` 的替代呈現

這是本節的核心交付。前三項現在**只活在 `title` 屬性裡**，鍵盤與觸控使用者完全拿不到；第四項有可見版本但**只在多選模式下才出現**（`MentionInbox.tsx:230` 的條件是 `merging && !selectable && blocked`），單選時仍然只剩 title：

| 現在 | 位置 | 改成 |
| :--- | :--- | :--- |
| 脈絡不連續的原因與時間區間、各 block 組成 | `DraftReplyWorkspace.tsx:52-63, 393` | `kind:'context'`、`status:'degraded'`、`critical:true`。reason 常駐可見，各 block 組成與時間區間放 `detail` |
| 被略過的圖片清單 | `:417-421` | `kind:'images'`，`detail[0].values = images_skipped` |
| 合併回覆的對象清單 | `:402-407` | `kind:'answering'`、`critical:true`，`values` 是「寄件人　時間」。**並且在送出確認對話框裡再列一次**——現在對話框只講數量（`:622-633`），而那正是最需要看清楚的一刻 |
| 無法勾選的原因 | `MentionInbox.tsx:199-205` | 不是 SSE 證據，但同一條規則：checkbox 改 `aria-disabled` ＋ `aria-describedby` 指向可見說明，且顯示條件從 `merging && !selectable` 放寬為 `!selectable && blocked` |

其餘 `title` 的處置見 §10.3。

---

## 6. 資訊架構與路由

### 6.1 為什麼只能用 hash 路由

三個事實相乘：

1. `vite.config.ts:8` 是 `base: './'`，註解寫明「FastAPI 會以靜態檔案托管 dist/，掛載路徑不固定」。
2. `dist/index.html` 因此引用 `./assets/index-*.js`（相對路徑）。
3. `dashboard/api/server.py` 的 `spa_fallback` 對任何找不到的檔案回 `index.html`。

結果：用 History API 深連結到 `/summary/AAAAxxx` 並重新整理，瀏覽器把 `./assets/index-*.js` 解析成 `/summary/assets/index-*.js` → 檔案不存在 → fallback 回 `index.html`（200、`text/html`）→ module script MIME 錯誤 → 白畫面。

替代方案是改 `base: '/'`，但那要推翻既有的掛載決定；而 hash 在這裡沒有實質損失（網址短到可以貼給同事）。

### 6.2 實作選擇：自刻薄層 hash router

不用 React Router v7 / TanStack Router。

1. hash 模式下它們的 loader / action / 型別產生器全是負擔，而 `dist/` 進版控，bundle 變大是每次 commit 的實際成本。
2. data router 的 loader 把資料綁回路由生命週期——那正是這個專案踩過坑才修掉的方向（`SummaryWorkspace.tsx:97-108`）。
3. 路由總數 < 15，無巢狀、無 SSR、無 code splitting 需求。

檔案：`src/lib/route.ts`（純函式，可測）＋ `src/router/useRouter.tsx`（Context＋`hashchange` 監聽）＋ `src/router/useRouteSync.ts`（route → store 單向同步）。

### 6.3 路由表

| Hash | 畫面 | 備註 |
| :--- | :--- | :--- |
| `#/` | → `#/summary` | `replace` |
| `#/summary` | 摘要工作台（未選 Space） | 768–1024 時等於 Space 清單 |
| `#/summary/:spaceKey` | 選定 Space | |
| `#/summary/:spaceKey?style=technical&limit=200` | 覆寫**這一次**的生成參數 | 不寫回偏好 |
| `#/mentions` | 收件匣 | |
| `#/mentions/:mentionId` | 選定一則 Mention | |
| `#/mentions/:mentionId?merge=47,48` | 合併選取（含順序，第一個是主要那則） | |
| `#/settings` | → `#/settings/reply` | |
| `#/settings/reply` | 回覆設定預設值 | |
| `#/settings/personas` / `#/settings/personas/:personaId` | Persona 管理／單筆（含淨化前後對照） | |
| `#/settings/prompts` / `#/settings/prompts/:promptId` | Reply Prompt Preset | |
| `#/settings/code-projects` / `#/settings/code-projects/:projectId` | 參考專案 | |
| `#/settings/spaces` | 釘選管理、私訊改名一覽 | |
| `#/settings/data` | 用量天數、摘要歷史分頁 | |
| `#/settings/diagnostics` | `/health` 診斷 | **未登入可看** |

### 6.4 Space id 進 URL

`spaces/AAAAxLxqJxY` 有斜線。`src/lib/route.ts` 提供兩個純函式：

- `toSpaceKey(id: string): string` — 砍掉 `spaces/` 前綴 → `AAAAxLxqJxY`
- `fromSpaceKey(key: string): string` — 補回 `spaces/`

436 個實測 id 都是這個形狀。防禦：不以 `spaces/` 開頭時退回 `encodeURIComponent`，還原時 `decodeURIComponent`。

### 6.5 未登入與回跳

**不做登入路由。** 登入是 `POST /auth/login`（伺服器端開瀏覽器完成 OAuth），不是 redirect，所以沒有真正的回跳問題。`LoginScreen` 仍是 App 層的 gate，hash 完全不動，auth 通過後 gate 打開就停在原本那頁。

只需兩件事：
1. `#/settings/diagnostics` 在 gate **之前**比對，未登入照樣渲染——「後端起來了嗎、AI 供應商設好了嗎」正是登入前最需要問的。
2. `api.ts:78` 的 `unauthorizedHandler` 補一行，把當下 hash 寫進 `sessionStorage`；登入成功後 `replace()` 回去（防使用者在登入畫面手動改過 hash）。

### 6.6 同步方向：單向

**URL 是 section / selectedSpaceId / selectedMentionId / mergeIds / 設定分頁的唯一真相。**

- `useRouteSync()` 監聽 route 變化 → 呼叫 `store.select(id)`。
- **去重放在 `useRouteSync` 裡，不要改 store 的 `select`**：`if (useSpacesStore.getState().selectedId !== id) select(id)`。把早退塞進既有的 `select` 會動到既有 export 的行為，違反 §17 紅線 3；放在同步器裡效果一樣而且不碰 store。
- **使用者點清單時呼叫 `navigate()`，不直接呼叫 `select()`**——這樣「點擊」與「貼網址」走同一條路徑，只有一種行為要維護。

**資料載入不進 route loader，一律留在 store。** 這是既有架構的正確決定，不要動它。

---

## 7. App shell 與掛載策略

### 7.1 結構

```
<AppShell>                        route gate + 全域快捷鍵 + 命令面板 + live region
├─ <SkipLink/>                    跳到主要內容（左欄有 436 筆，鍵盤要 Tab 很久）
├─ <TopBar/>            h-12      PulseMark │ 工作台切換(nav) │ 麵包屑 │ ⌘K │ 證據鈕 │ 齒輪 │ 主題 │ 登出
├─ <div flex-1 min-h-0>
│  ├─ <aside w-inbox>             ← 兩個都常駐掛載
│  │   ├─ <SpacesRail/>                   active = summary
│  │   └─ <MentionInbox/>                 active = mentions
│  ├─ <main flex-1 min-w-0 id="main" tabIndex={-1}>
│  │   ├─ <SummaryWorkspace/>
│  │   └─ <DraftReplyWorkspace/>
│  └─ <EvidenceColumn/>  ≥1280 常駐 w-rail ／ 1024–1280 抽屜
├─ <SettingsOverlay/>             #/settings/* 覆蓋在上層，工作台仍掛著
├─ <CommandPalette/>
└─ <SmallScreenNotice/>           <768
```

### 7.2 保留掛載：用 `inert`，不用 `display:none`

`SpaceList` 用 `@tanstack/react-virtual`，在 `display:none` 的容器裡量到的高度是 0，切回來會重新 measure（閃一下、捲動位置歸零）。

做法：非 active 的那半用 `absolute opacity-0 -z-10 pointer-events-none` **保留尺寸**，並加 React 19 原生支援的 `inert` 讓它退出 tab 序與無障礙樹。**只靠 `aria-hidden` 擋不住 Tab。**

### 7.3 保留掛載的真實成本：重繪

兩邊同時串流時，隱藏那半的 `Markdown` 仍會每個 chunk 重新 parse 全文（`marked`）。現在的條件渲染「意外地」避開了這件事——卸載了就不重繪。

對策：`Markdown` 元件加 `active?: boolean` prop，`active === false` 時暫停跟隨串流（保留最後一次渲染結果），切回來時一次補上。

**P2 必須用 React DevTools Profiler 實際量一次**（兩邊各跑一份長摘要，看 commit 時間），不要先猜。

### 7.4 副作用搬家

| 現在 | 搬到 | 為什麼 |
| :--- | :--- | :--- |
| `MentionInbox.tsx:51-55` 的 45 秒 `setInterval` | `store/mentions.ts` 的 `startPolling()` / `stopPolling()`，由 AppShell 在登入後啟動一次 | 輪詢屬於這份資料，不屬於這個畫面；使用者在摘要頁時 badge 也該動。**vitest 是 node 環境**，計時器只能在 action 內用 `globalThis.setInterval`，module top-level 不可碰 `window` |
| `SpacesRail.tsx:40-42` 的首次 `load()` | AppShell bootstrap | 釘選、命令面板、Reference Space 三處都要 spaces |
| `CodeProjectSettings.tsx:36-38` ＋ `DraftReplyWorkspace.tsx:106-108` 的 `load()` | `store/codeProjects.ts` 加 `loaded` 旗標（照 `store/replySettings.ts:198` 的寫法） | 目前**完全沒有去重**，這是切頁籤重打 API 的直接原因 |
| `UsagePanel.tsx` 的 `useState` + `useEffect` | 新 `store/usage.ts`（含 `days`） | days 要能從設定頁改 |
| 兩個工作台的條件式 `reset()` | 保留當第二道防線，主要改由路由同步器呼叫 `select()` | |

---

## 8. 設定中心

### 8.1 快速切換 vs 管理

判準：**「這次不一樣」留工作區，「以後都這樣」進設定中心。**

| 工作區（`QuickReplySettings`） | 設定中心 |
| :--- | :--- |
| 口氣下拉（含「跟隨預設」） | 我的預設口氣 |
| Persona 下拉（只列 enabled，含「不使用」） | 匯入／刪除／啟停用／refresh／**看淨化掉了什麼** |
| 自訂提示 textarea ＋ Preset 下拉 | Preset CRUD |
| Sepia 勾選 | 預設開關、規則版本 |
| 參考專案環境勾選矩陣 | 專案 CRUD、分支對應、**建檔前 dry-run 驗證**、**預設專案與環境**（`default_code_project_id` / `default_code_environment`） |
| 供應商下拉 | 預設供應商 |
| — | 釘選 Space 管理、私訊改名一覽 |
| — | 用量天數（1–90）、摘要歷史分頁（1–1000） |
| — | `/health` 診斷 |

`ReplySettings.tsx:401-416` 那顆「設為預設」從主要位置移到設定中心（它的語意是「以後都這樣」，放在工作區容易被當成「套用」），但**工作區保留一個低調的溢出選單項，不移除功能**。

### 8.2 入口與返回

**入口**：頂列齒輪、工作區各區塊的「管理」連結（原本開 Dialog 的兩顆改成 `navigate`）、命令面板、以及證據欄的 `action`（例：code-ref 顯示「這個分支沒有找到相符的程式碼」時直接連 `#/settings/code-projects/:id`）。

**返回**：設定中心是**覆蓋層路由**，工作台仍掛在後面（串流不中斷、狀態不掉）。關閉＝`history.back()`；若是直接貼連結進來（沒有上一頁）fallback 到 `#/summary`。**不需要 `returnTo` 參數。**

### 8.3 釘選 Space 的實作陷阱

`PATCH /api/v1/preferences` 的 `null` 語意在新舊欄位之間不一致（`docs/api-contract.md:130` 有對照表）：

- `pinned_space_ids` 送 `null` ＝ **不改**（等同沒帶這個欄位）
- `default_reply_tone` / `default_persona_id` / `default_reply_prompt_id` / `default_sepia_enabled` 送 `null` ＝ **清除**

**所以取消最後一個釘選要送 `[]`，送 `null` 會靜默地什麼都沒發生。**

另外 `store/spaces.ts:83-89` 的 `filterSpaces` **只過濾、零排序**。釘選要排在最前面，得自己加排序（釘選優先，其次照現有順序），並確保虛擬滾動的 `count` 與索引跟著走。

---

## 9. 元件清單與拆檔

### 9.1 目標

**元件與 hook 檔 < 250 行**（`.tsx` 與 `hooks/`）。`store/` 與 `lib/` 另計，理由見下方。

> **2026-09-10 修訂**：原文寫的是「最大檔 < 250 行」，唯一例外是 `lib/types.ts`。
> 實作完成後盤下來，那條門檻只在**元件**上站得住，所以改成分開計算。
>
> 這條門檻當初的理由寫得很清楚：**「兩個 913／650 行的元件難維護」**。那是元件
> 的問題——一個檔同時管版面、狀態、副作用與四五個子區塊，改任何一處都要先讀完
> 整份。`store/` 與 `lib/` 不是那個形狀：zustand store 是一組扁平的 action，
> `lib/api.ts` 是一張端點對照表，`lib/evidence.ts` 是一串純函式。把它們拆成
> 「上半部／下半部」換到的只是「每個檔都在 250 行以下」這個數字，付出的是多一層
> import 間接與「這個 action 在哪一半」的認知成本——那不是同一個問題的解法。
>
> 所以規格改成：**元件與 hook 一律 < 250**（實作後全部達成，最大 243 行的
> `SpaceList.tsx`）；`store/` 與 `lib/` 不設行數門檻，但**新增第二個責任時要拆**
> （判準是責任數量，不是行數）。`lib/types.ts` 因此不再需要當成「例外」——它是
> 純型別、鏡射後端契約，本來就在另計的範圍裡。
>
> 目前 `store/` 與 `lib/` 超過 250 行的六個檔（`lib/types.ts` 560、
> `store/replySettings.ts` 413、`store/draft.ts` 370、`lib/evidence.ts` 360、
> `lib/api.ts` 321、`store/mentions.ts` 251）依此判準保持原狀。
> `lib/types.ts` 也在這一列裡——它不再是「例外」，而是本來就在另計的範圍。

兩個大檔的去向不同，措辭要分清楚：

- `src/components/ReplySettings.tsx`（913 行）→ **檔名消失**，內容分散到 `components/draft/QuickReplySettings.tsx` 與 `components/settings/` 底下數個檔。
- `src/components/DraftReplyWorkspace.tsx`（650 行）→ **檔名保留但搬家並瘦身**：移到 `src/components/draft/DraftReplyWorkspace.tsx`，只留下版面骨架與 reset 契約（約 90 行），其餘拆成同目錄的兄弟檔。
- `src/components/CodeProjectSettings.tsx`（246 行）→ **檔名消失**，內容進 `components/settings/CodeProjectsPage.tsx` 與 `CodeProjectForm.tsx`。

### 9.2 目錄結構

```
src/app/
  AppShell.tsx              route gate、全域快捷鍵、live region、bootstrap        ~200
  TopBar.tsx                PulseMark、工作台 nav、麵包屑、⌘K、證據鈕、齒輪、主題、登出  ~120
  SmallScreenNotice.tsx     <768 說明頁 ＋ localStorage 逃生門                    ~70
  PulseMark.tsx             脈搏線 inline SVG（頂列與登入畫面共用）               ~20
  Pane.tsx                  常駐掛載的其中一半（§7.2 的 inert 手法）              ~25
  StreamLiveRegions.tsx     §10.6 的兩個 sr-only live region                     ~30
  MasterDetailBack.tsx      768–1024 主從切換的返回麵包屑（§12）                  ~35

src/router/
  useRouter.tsx             Context ＋ hashchange 監聽 ＋ navigate/replace         ~110
  useRouteSync.ts           route → store 單向同步                                ~70

src/components/draft/
  DraftReplyWorkspace.tsx   容器：版面骨架 ＋ 換 Mention 的 reset 契約             ~90
  MentionSourceCard.tsx     原始 Mention 卡（狀態 badge、原文）                    ~70
  DraftSetupPanel.tsx       左側設定堆疊的排版容器                                 ~80
  ReferenceSpacePicker.tsx  Reference Space 搜尋 ＋ 抓取則數 ＋ 清單               ~110
  CodeRefPicker.tsx         參考專案環境勾選矩陣                                   ~95
  QuickReplySettings.tsx    工作區快速切換（原 ReplySettings 的 63–424，去掉兩個 dialog）  ~200
  GenerateButton.tsx        產生／停止串流（含合併字樣）                            ~55
  DraftOutputPane.tsx       右側產出容器 ＋ 串流宣告協調                            ~85
  DraftReplyEditor.tsx      建議回話 Textarea ＋ 送出鈕                            ~70
  SendReplyConfirm.tsx      送出二次確認（**含 answering 名單**）                   ~90

src/components/evidence/
  EvidenceColumn.tsx        外殼：標題列、⟳、底部帳本合計                          ~120
  EvidenceList.tsx          逐項渲染，critical 展開、其餘 <details>                ~130
  EvidenceDrawer.tsx        1024–1280 的抽屜包裝                                   ~90
  CodeRefEvidence.tsx       code_refs 的多行結構（專案／分支／命中檔案）            ~90

src/components/settings/
  SettingsOverlay.tsx       覆蓋層外框、分頁 nav、關閉、focus trap                 ~110
  ReplyDefaultsPage.tsx     四項預設 ＋ 供應商預設 ＋ 儲存                          ~180
  PersonasPage.tsx          清單、啟停用、refresh、刪除                            ~170
  PersonaImportForm.tsx     GitHub／URL 匯入（原 dialog 上半）                     ~130
  PersonaProfileView.tsx    淨化後 profile                                        ~120
  PersonaSanitizeDiff.tsx   **淨化前後對照**（`?include_raw`，新建）                ~90
  ReplyPromptsPage.tsx      Preset 清單 ＋ 新增／編輯                              ~190
  CodeProjectsPage.tsx      清單 ＋ 分支矩陣 ＋ verify                             ~150
  CodeProjectForm.tsx       新增／編輯表單（**含建檔前 dry-run**）                  ~120
  SpacePrefsPage.tsx        釘選管理、私訊改名一覽                                 ~160
  DataPage.tsx              用量 1–90 天、摘要歷史 1–1000 分頁                     ~170
  DiagnosticsPage.tsx       /health ＋ collector ＋ 供應商可用性 ＋ Sepia 版本      ~130

src/components/common/
  EmptyState.tsx / ErrorState.tsx / LoadingSkeleton.tsx / SkipLink.tsx / InlineDetails.tsx

src/components/summary/       ← 2026-09-10 新增（拆 SummaryWorkspace 383 行）
  SummaryToolbar.tsx        目標 Space／風格／供應商／則數／產生草稿／開始停止    ~195
  SummaryOutput.tsx         訊息預覽、空狀態、來源標記、複製／推播、Markdown      ~155
  PublishConfirm.tsx        推播回 Google Chat 的二次確認（**不可撤回**）          ~65

src/components/inbox/        ← 2026-09-10 新增（拆 MentionInbox 337 行）
  MentionCard.tsx           一則 Mention（勾選、內文、不可合併的原因、狀態鈕）    ~130
  MergeBar.tsx              合併列（**回話只送到第一則所在的討論串**）             ~40

src/components/preview/      ← 2026-09-10 新增（拆 SpaceMessagePreview 287 行）
  ThreadRow.tsx             收合起來的一整串                                     ~95
  MessageRow.tsx            一則訊息（`data-message-name` 是去重的量測點）        ~35

src/components/
  CommandPalette.tsx        ⌘K 面板                                              ~220
  ShortcutHelp.tsx          `?` 快捷鍵說明覆蓋層（2026-09-10 新增）               ~120

src/lib/
  evidence.ts               SseMeta ＋ polish → EvidenceBundle（純函式）           ~240
  route.ts                  hash 解析／組裝／toSpaceKey／fromSpaceKey（純函式）     ~130
  listNavigation.ts         按鍵 → 下一個 index（純函式）                          ~50
  streamAnnouncements.ts    狀態轉換 → 宣告字串（純函式）                          ~60
  commands.ts               命令清單與過濾（純函式）                               ~150
  hotkeys.ts                快捷鍵比對與 isTypingTarget（純函式）                  ~135
  shortcutHelp.ts           `?` 說明表（與 hotkeys.ts 之間有漂移守衛）             ~135
  mergeCopy.ts              MERGE_BLOCK_LABEL 從 merge.ts 搬出來                   ~20

src/hooks/
  useVirtualListKeyboard.ts / useStreamAnnouncer.ts / useGlobalHotkeys.ts / useBreakpoint.ts

src/store/
  ui.ts                     抽屜開合（分斷點記憶）、命令面板開合                    ~90
  usage.ts                  用量（含 days）                                       ~80
```

### 9.3 介面原則

子元件**不接大 props 物件**，各自用細 selector 讀 store（`useDraftStore(s => s.replyText)`）。只有 `mention` 與純顯示用的 `EvidenceItem[]` 走 props。

**這一條是效能修復不是風格偏好**：`DraftReplyWorkspace.tsx:76-99` 現在用無 selector 的 `useDraftStore()`，拆檔時若照抄那一行，就是把「每個 chunk 重繪整棵子樹」複製到每個子檔。

### 9.4 死碼處置

| 檔案 | 處置 |
| :--- | :--- |
| `ui/scroll-area.tsx` | **刪除**。它會接管 scroll container，與 `useVirtualizer` 的 `getScrollElement` 直接衝突 |
| `ui/tooltip.tsx` | **刪除**。觸控裝置摸不到，本次改版禁止用它承載唯一資訊 |
| `ui/skeleton.tsx` | **復活**，給 `LoadingSkeleton` 用。先處理 `animate-pulse` 的 reduced-motion 降級 |
| `ui/badge.tsx` | **復活**，`rounded-4xl` 改 `rounded-sm`，修掉 `overflow-hidden` 裁切 focus ring 的問題 |
| `ui/separator.tsx` | 保留，設定中心會用 |

---

## 10. 可及性規格

### 10.1 Landmark 與導覽

- `SkipLink` 放在 root 第一個節點，`sr-only focus:not-sr-only`，指向 `#main`。
- `<main id="main" tabIndex={-1}>`。
- 兩個 `<aside>` 與 `<nav>` 各補 `aria-label`。
- `CollectorPanel` / `UsagePanel` 等區塊用 `<section aria-labelledby>` 指向內部標題。

### 10.2 頂列工作台切換

**改成 `<nav>` ＋ `<a href="#/summary">` ＋ `aria-current="page"`，不要補 tablist 語意。**

路由化之後它們就是連結；硬套 tablist 會遇到 panel 分居 `<aside>` 與 `<main>` 兩個子樹、`aria-controls` 無處可指的問題。

現在 busy 點用 `title` ＋ `aria-label`（`App.tsx:251-261`），改成頁籤可及名稱內的 `<span className="sr-only">（正在生成）</span>`，狀態轉換由單一 app 級 live region 宣告一次。

### 10.3 `title=` 逐條處置

規則：**決策必需 → 可見文字；補充細節 → `<details>`；與可見文字重複 → 刪。**

全專案 29 處，逐一處置後 **DOM 上的 `title` 屬性歸零**；剩下 2 處是 React 元件 prop（`SummaryWorkspace.tsx:350` 與 `DraftReplyWorkspace.tsx:617` 的 `<ConfirmDialog title=…>`），那是對話框標題、不是 tooltip，予以保留。

| 位置 | 內容 | 處置 |
| :--- | :--- | :--- |
| `DraftReplyWorkspace.tsx:393` | 脈絡不連續原因、時間區間、block 組成 | → 證據欄 `context` 項（§5.7） |
| `:402-407` | 合併回覆對象清單 | → 證據欄 `answering` 項 ＋ **送出確認框再列一次** |
| `:417-421` | 被略過的圖片 | → 證據欄 `images` 項的 `<details>` |
| `:431` | 「本次實際使用的 AI 供應商與模型（來自 meta 事件）」 | → 證據欄 `model` 項的可見說明，並改寫文案（§13） |
| `:478-482` | Sepia 未採用原因 | 已有可見段落（`:514-522`），**刪 title**，badge 改 `aria-describedby` 指向該段 |
| `:204-208` | 「手動指定」的說明 | → 改成可見一行 |
| `MentionInbox.tsx:199-205` | 不可勾選的原因 | 刪 title；顯示條件放寬為 `!selectable && blocked`；checkbox 改 `aria-disabled` ＋ `aria-describedby` |
| `SpacesRail.tsx:71` | 「跳過 5 分鐘快取，向 Google 重新取回」 | 併進底部「快取於 X」腳註，刪 title |
| `SpaceList.tsx:128` | 「這個名字是猜的，可以自己取」 | 升為列的可見副標記「名稱為推測」 |
| `SummaryWorkspace.tsx:214` | 「改完離開這個欄位就會記住」 | 改 Label 下的可見 hint ＋ `aria-describedby` |
| `SummaryWorkspace.tsx:306` | 同 `:431` | 同上 |
| `ProviderSelect.tsx:82` | 「（PATCH /preferences）」是端點名 | **純刪**（按鈕文字已足夠） |
| `ProviderSelect.tsx:113` | `title={item.reason}`，但同一個 `item.reason` 在 `:125` 已經可見渲染 | **純刪**（真重複） |
| `ReplySettings.tsx:408` | 列舉了「會存下哪四項」，但可見文字只有「設為預設」——**這不是重複，是唯一資訊** | 這顆按鈕依 §8.1 移到設定中心，該處把它改成可見說明；工作區留下的溢出選單項不需要 title |
| `ReplySettings.tsx:159` | 摺疊摘要行同時是可見文字與 title | 純刪 title（`truncate` 造成的截斷改用展開，不用 tooltip） |
| `ReplySettings.tsx:193` | `title={tone.description}`，同一段文字在 `:197` 已經可見 | **純刪**（真重複） |
| `ReplySettings.tsx:223, 299` | 「匯入與管理 Persona」「管理常用提示詞」 | 這兩顆依 §8.2 改成連到設定中心的連結，連結文字自己就說清楚了 → 刪 title |
| `ReplySettings.tsx:364` | Sepia 的說明與**不可用原因 `sepia.reason`**——後者是唯一資訊 | 說明改成勾選框下方的可見一行；`sepia.reason` 在不可用時改成可見的停用理由 |
| `ReplySettings.tsx:624` | 「重新從來源取得（會更新 commit）」 | 移到設定中心的 Persona 頁，改成按鈕旁的可見說明 |
| `SummaryWorkspace.tsx:224` | 「針對這個對話裡對方最後說的話，產生一則回覆草稿」——說明按鈕實際做什麼，是唯一資訊 | 改成按鈕下方的可見一行 |
| `SpaceMessagePreview.tsx:128` | 「重新讀取（訊息是即時取回的，不進資料庫）」 | 改成預覽區底部的可見腳註 |
| `ThemeToggle.tsx:18` | 與 `aria-label` 功能重疊 | 刪 title，改用 `aria-label`（三態切換後要說明**目前解析結果**，例如「跟隨系統（目前深色）」） |
| `App.tsx:254` | 「正在生成，切到別的頁籤也會繼續」 | 見 §10.2：改成頁籤可及名稱內的 `sr-only` 文字 |
| `DraftReplyWorkspace.tsx:442, 450, 458` | 口氣／Persona／自訂提示三個 badge 的說明 | 三者都變成證據欄「回話設定」組的具名列，標籤本身就是說明 |

處置完之後，`title=` 只准出現在白名單（§15.3 的守門測試會擋）。

### 10.4 參考專案環境 chip

`DraftReplyWorkspace.tsx:314-333` 用 `<label>` 包 `sr-only` 的 `<input type=checkbox>`，**label 上沒有 focus-visible 樣式，鍵盤 focus 完全看不見**。

- label 加 `has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-signal has-[:focus-visible]:outline-offset-2`
- `<ul>` 加 `role="group"` ＋ `aria-labelledby`
- chip 的可及名稱要含專案名（現在只有「正式 main」，脫離視覺無法辨識）

### 10.5 虛擬清單鍵盤導航（436 筆）

**選 roving tabindex，不要 `aria-activedescendant`。** 後者要求被指向的節點常駐 DOM，而虛擬滾動只掛可視範圍 ＋ overscan 12 列，active 一捲出去多數輔助技術就失去目標。

- `<ul role="listbox">`（複選模式加 `aria-multiselectable`）＋ `<li role="option" aria-selected>`
- `tabIndex` 只有 active 那一列是 `0`，其餘 `-1`
- 支援 `↑` `↓` `Home` `End` `PageUp` `PageDown` `Enter`
- 移動時先 `virtualizer.scrollToIndex({ align: 'auto' })`（**不要 `center`**），再在 `useLayoutEffect` 依 `data-index` 取焦
- 複選模式現在那顆 `pointer-events-none tabIndex={-1}` 的 Checkbox（`SpaceList.tsx:91-97`）是假語意，改由 `aria-selected` 承擔
- 按鍵 → index 的計算抽到 `lib/listNavigation.ts`，node 環境純函式測

### 10.6 串流的螢幕閱讀器宣告（三層）

1. **內容層**：`Markdown` 容器**絕不設 `aria-live`**。每個 chunk 都重寫 innerHTML，設了等於整段重念。串流中只設 `aria-busy="true"`。
2. **狀態層**：app 級 `<div role="status" aria-live="polite" className="sr-only">`，只在**狀態機轉換**時寫入里程碑：
   - 開始生成
   - `splitDraft().replyStarted` 由 false → true（「脈絡分析完成，開始寫建議回話」）
   - 完成（含字數與 Sepia 結果）
   - 錯誤走 `role="alert"`
3. **節流層**：需要進度感時用 10 秒 `setInterval` 宣告一次，**不隨 chunk**。

字串產生抽到 `lib/streamAnnouncements.ts` 純函式。

**完成時不自動搶焦點**（會打斷正在讀舊內容的人），改在宣告文字裡告知快捷鍵。

### 10.7 其他

- `refLimitError` / `limitError` 現在只有 `aria-invalid`，要補 `aria-describedby` ＋ `role="alert"`。
- `UsagePanel` 的 table 補 `<caption className="sr-only">` 與 `<th scope="col">`。
- `ConfirmDialog` 的 `<pre>`（`:56`）補 `tabIndex={0}` ＋ `role="region"` ＋ `aria-label`，讓鍵盤可捲。
- 點擊區域：硬底線 24px（WCAG 2.2 §2.5.8 AA），房規 32px（`--spacing-tap`）。凡誤觸有不可逆後果的（送出回話、標記已處理、收件匣合併 checkbox）一律 ≥32px 命中區，用 padding 或 `::before` 擴張，不放大視覺尺寸。
- disabled 一律用 `--disabled-fg`，不用 `opacity`。`MentionInbox.tsx:188` 的 `opacity-45` 讓「不可合併的原因」掉到約 2.7:1。

### 10.8 對比目標

| 類別 | 目標 | 說明 |
| :--- | :--- | :--- |
| 決策性文字（草稿內文、Mention 原文、證據欄的值） | **≥7:1** | 送出不可撤回且閱讀是趕的 |
| 次級文字與標籤 | ≥4.6:1 | |
| 承載語意的規線、focus 環、只有字符的指示器 | ≥3:1 | WCAG 1.4.11 |
| 純裝飾分隔線 | 無要求 | 見 §3.2 |
| disabled | ≥2:1 | |

§3.3 的 token 已經按這個表配過（計算值見註解）。**實作時要用 DevTools 對比檢查器對實際渲染結果複驗**，特別是半透明疊色。

---

## 11. 快捷鍵與命令面板

### 11.1 快捷鍵表

| 鍵 | 動作 |
| :--- | :--- |
| `⌘K` / `Ctrl+K` | 命令面板（**要 `preventDefault`**，Firefox 綁了搜尋） |
| `Esc` | 由內而外關閉：對話框 → 命令面板 → 抽屜 |
| `g s` / `g m` / `g ,` / `g h` | 摘要 / 收件匣 / 設定 / 診斷（兩鍵序列，1 秒逾時） |
| `/` | 焦點跳到左欄搜尋框 |
| `⌘J` | 開關證據欄 |
| `↑` `↓` | 左欄清單移動（走 `navigate`，不直接改 store） |
| `Enter` | 開啟高亮項（**先過 `isComposing`**） |
| `⌘Enter` | 開始摘要／產生 Draft Reply |
| `⌘.` | 停止串流 |
| `⌘⇧C` | 複製 Markdown |
| `[` / `]` | 上一則／下一則 Mention |
| `?` | 快捷鍵說明 |

**送出回話與推播回 Google Chat 沒有快捷鍵，也不進命令面板。** 不可撤回的動作不該有肌肉記憶，而命令面板「打字 → Enter」正是最容易誤觸的介面。

### 11.2 與 `lib/keyboard.ts` 整合

`isComposing()` 目前只吃 React `KeyboardEvent`（讀 `event.nativeEvent.isComposing`）。全域監聽拿到的是原生事件，簽章要改成同時吃兩種：`'nativeEvent' in e ? e.nativeEvent : e`。

**這會動到 `lib/keyboard.test.ts`，是本次唯一一處刻意的既有 lib 改動**（§17 紅線 3 的例外），測試要同步更新。

新增 `isTypingTarget(el)`：`input` / `textarea` / `[contenteditable]` 內單鍵快捷鍵不觸發，`⌘K` 與 `Esc` 例外。命令面板開啟時全域快捷鍵整組停用。

### 11.3 焦點管理

- 命令面板開啟：焦點進搜尋框，用 `aria-activedescendant` 高亮結果（**不移動真實焦點**，打字才不中斷）；關閉時焦點還給觸發元素。
- 抽屜開啟：焦點進第一個可聚焦元素；`Esc` 還焦點。
- 隱藏的工作台加 `inert`。

---

## 12. 響應式

| 斷點 | 版面 |
| :--- | :--- |
| **≥1280** | 三欄常駐：左 288（`--container-inbox`）／中 flex／右 320（`--container-rail`） |
| **1024–1280** | 兩欄 ＋ 證據抽屜（右側滑出）。觸發：頂列證據鈕（`attentionCount > 0` 時亮點）、`⌘J`。**不自動打開**——會蓋住正在讀的草稿。開著時**不 inert 主區**（要能邊看邊改），`Esc` 關閉 |
| **768–1024** | 主從切換：清單與工作區同時只顯示一個，麵包屑返回。這與 URL 天然對應（`#/summary` = 清單、`#/summary/:key` = 工作區），**不必另寫狀態**——這是選 URL 路由的額外報酬。證據仍是抽屜 |
| **<768** | `SmallScreenNotice`：說明為什麼（分支名、commit sha、程式碼片段在手機上讀不了）、列出這台裝置仍能做的事（看診斷、看待處理數量），並給一個存在 `localStorage` 的「我知道，仍要繼續」**逃生門**——擋死會讓人在緊急時完全用不了 |

抽屜開合狀態存 `localStorage`，且**每個斷點各記一份**（在 1280 開著不代表 1024 也要開著）。

斷點判斷以 Tailwind CSS 為主；只有「抽屜 vs 常駐」需要 JS：`useBreakpoint()` 用 `matchMedia`，**沒有 `window` 時回傳預設值**（node 測試環境）。

---

## 13. 文案規範與改寫

### 13.1 必須原樣保留的統一語言

來自 `CONTEXT.md`：**Viewer／Space／Space 成員身分／Summary／Mention／已處理／Draft Reply／Reference Space／Reply Tone（介面上寫「口氣」）／Persona／Reply Prompt Preset／潤稿／脈絡分析／建議回話／Sepia**。

這些不是「可以翻成中文」的術語，是刻意選定的詞彙。特別注意 `CONTEXT.md:45` 明列 Reply Tone 的 _Avoid_ 清單（摘要風格、Summary Style、style、口吻設定）——**不要把「口氣」順手改成「語氣風格」**。

### 13.2 改寫清單

| 位置 | 問題 | 改寫方向 |
| :--- | :--- | :--- |
| `LoginScreen.tsx:78-81` | 「Phase 1 之前留下的 `config/google_chat_token.json`」「只有三個 chat scope」「`CHATPULSE_BOOTSTRAP_USER_ID`」 | 主文只講做什麼：「把這台機器上既有的授權檔匯入成你的身分。適用於在 ChatPulse 加入 Google 登入之前就設定過的環境。」路徑與環境變數名移到診斷頁 |
| `SummaryWorkspace.tsx:306`、`DraftReplyWorkspace.tsx:431` | 「（來自 meta 事件）」是 SSE 協定欄位名 | 「本次實際使用的供應商與模型」 |
| `ProviderSelect.tsx:82` | 「（PATCH /preferences）」 | 刪（按鈕文字已足夠） |
| `CodeProjectSettings.tsx:73` | 「登錄本機的 git repo」——`git repo` 是實作詞彙 | 改「登錄這台機器上的程式碼資料夾」 |
| `CodeProjectSettings.tsx:188` | 標籤「本機路徑（絕對路徑）」可讀但偏系統視角 | 改「專案資料夾路徑」，`:191` 的路徑值與 `:104` 的顯示值都保留原樣 |
| `ReplySettings.tsx:392-397` | `sepia v{version} @ {sha}` 常駐 monospace 雜訊 | 移到診斷頁 |
| `DraftReplyWorkspace.tsx:213`（樣式在 `:202`） | badge「手動指定」是 `state='manual'` 的直譯 | 改「自選對話」，原 `:204-208` title 的說明改成可見一行 |
| `CollectorPanel` 的「實作」列 | 直接顯示後端實作名 | 移診斷頁，主面板只留運行狀態與上次輪詢時間 |

### 13.3 一份要順手修正的文件漂移

`docs/api-contract.md:684-685` 寫「元件 unmount 時要 `AbortController.abort()`，否則串流會繼續跑」，但程式碼**刻意不這麼做**——`DraftReplyWorkspace.tsx:125-130` 與 `SummaryWorkspace.tsx:110-112` 都有註解說明理由（切頁籤不該中止已經在燒額度的生成，而且後端要整段跑完才落盤）。

改成保留掛載之後這個矛盾自然消失，但契約文件那一條要一併更新，否則下一個人會照它去加 abort。

---

## 14. 實作階段與驗收條件

每個 Phase 結束時 **app 必須是可用的**，並且各自一個 commit（Conventional Commits、繁體中文）。

### P0 — 設計 token 地基

**做**：貼上 §3.3 的完整 token 區塊 ＋ §3.4 的別名 shim ＋ `@fontsource-variable/geist-mono` ＋ CJK fallback 鏈 ＋ 新增 `PulseMark` 並在頂列與登入畫面換掉 `ZapIcon` ＋ favicon 換色 ＋ 處理既有 `index.css` 的其餘段落（§3.6）。

**這個 Phase 唯一允許的 `.tsx` 改動是把 `<ZapIcon …/>` 換成 `<PulseMark …/>`**（`App.tsx:137`、`LoginScreen.tsx:25`，各一行）。外層那個 `<span className="… bg-sky-500/15 text-sky-500 ring-1 ring-sky-500/25">` 底色圈**原封不動留著**——它會經由 §3.4 的別名 shim 自動吃到新的訊號色，到 P5 才改寫成 `bg-signal-wash text-signal ring-signal-line`。

**P0 不得調整任何 className 的顏色或字級。** 這樣 P0 出問題時可以一眼判斷是 token 的錯還是 call site 的錯。

`PulseMark` 的契約：`interface PulseMarkProps { className?: string }`，內部是一個 `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round">` 包一條 `<path d="M4 12h3l2-6 4 12 2-6h5" />`，`aria-hidden="true"`。尺寸由 `className` 的 `size-*` 控制，與 lucide 的用法一致。

**驗收**
- [ ] `npm --prefix dashboard/frontend run typecheck` 通過
- [ ] 既有測試全綠且數量不減（基準：**159 項 / 11 檔**，2026-09-09 實跑）
- [ ] 淺色／深色各截一次圖，107 處具名色全部呈現新配色
- [ ] **觸發一則 toast、開一次 Dialog、開一次 Select**，確認背景、邊框、圓角都正常——這三個元件直接讀原始 CSS 變數（§3.5），相容層漏一個就會靜默壞掉
- [ ] 中文字實際落在 fallback 鏈上：DevTools → Elements → Computed → **Rendered Fonts**，對一段中文確認顯示的是 `PingFang TC`（macOS）而不是某個未預期的字體；Latin 與數字確認是 `Geist Variable`／`Geist Mono Variable`
- [ ] `PulseMark` 出現在頂列與登入畫面，`ZapIcon` 兩處都已移除

### P1 — hash router 地基（無視覺變化）

**做**：`lib/route.ts` ＋ `useRouter` ＋ `useRouteSync`；`store/codeProjects.ts` 加 `loaded` 旗標；新增 `store/usage.ts`。

**驗收**
- [ ] 貼 `#/mentions/47` 直接開到那一則
- [ ] 在任一畫面重新整理，停在原地不跳回摘要頁
- [ ] 瀏覽器上一頁／下一頁行為正確
- [ ] `#/settings/diagnostics` 在未登入狀態下看得到
- [ ] **畫面與 P0 肉眼無差異**
- [ ] 新增 `lib/route.test.ts`，涵蓋 `toSpaceKey`／`fromSpaceKey`／不合形狀的 id／query 解析

### P2 — 保留掛載

**做**：`AppShell` 重構、`inert`、副作用搬進 store（§7.4）、`Markdown` 的 `active` prop。

**驗收**
- [ ] 兩邊同時串流時切換工作台，兩邊都繼續且畫面不重置
- [ ] Network 面板數得出 `/api/v1/mentions` 是 45 秒一次，切工作台不會多打
- [ ] `/api/v1/code-projects` 全程只打一次
- [ ] 左欄虛擬清單的捲動位置在切換工作台後保留
- [ ] **React DevTools Profiler 實際量過**：兩邊各跑一份長摘要，記錄隱藏側的 commit 時間，並附在 commit message 或 PR 描述裡

### P3 — 設定中心（必須早於 P4）

**做**：拆 `ReplySettings.tsx`（913）與 `CodeProjectSettings.tsx`（246）、overlay route、釘選 Space、Persona 淨化前後對照、專案 dry-run 驗證、用量天數、摘要歷史分頁、診斷頁。

**驗收**
- [ ] `src/components/ReplySettings.tsx` 與 `src/components/CodeProjectSettings.tsx` **兩個檔名都不存在**（`DraftReplyWorkspace.tsx` 此時仍在原位，P4 才搬到 `components/draft/`）
- [ ] `src/components/settings/` 與 `src/components/draft/` 底下每個檔 < 250 行。**此時 `DraftReplyWorkspace.tsx` 仍會超標（它到 P4 才拆），這是預期的**——全域「最大檔 < 250 行」的檢查排在 P4
- [ ] 設定改完關閉，回到原本那個 Space／Mention，且串流沒斷
- [ ] 釘選的 Space 排在清單最前面；**取消最後一個釘選送的是 `[]` 不是 `null`**（Network 面板確認 request body）
- [ ] Persona 詳情看得到「淨化掉了什麼」
- [ ] 新增參考專案時可以在存檔前驗證分支
- [ ] 用量天數可切換（1–90），摘要歷史可分頁
- [ ] 可設定預設參考專案與環境，且草稿工作區開啟時已預選
- [ ] `#/settings/diagnostics` 顯示 `/health` 的 db／viewer_count／collector 實作／供應商可用性／Sepia 規則版本

> **為什麼 P3 必須早於 P4**：`DraftReplyWorkspace` 內部已經是 `280px + 1fr` 兩欄，外層再三欄等於實際四欄。設定沒抽走之前，右邊擠不出可用寬度，做出來只會是另一個雜物抽屜。這是相依，不是偏好。

### P4 — 證據欄

**做**：`lib/evidence.ts` ＋ `EvidenceColumn` / `EvidenceList` / `EvidenceDrawer` / `CodeRefEvidence` ＋ 移除承載唯一資訊的 `title=`（§5.7）＋ 送出確認框列出 answering 名單。

**驗收**
- [ ] 三項原本只活在 `title` 屬性裡的資訊（脈絡不連續原因、被略過的圖片、合併對象）＋ 一項只在多選模式才可見的（無法勾選的原因），**四項都做到鍵盤 Tab 到得了、螢幕閱讀器讀得到，且不分模式都可見**
- [ ] 送出確認框列出 `meta.answering` 每一則的寄件人與時間（不是只講數量）
- [ ] 餵一份缺 `context` 與 `image_count` 的舊版 meta，顯示 `missing`（dotted ＋ `—`）而不是崩潰或假裝 `ok`
- [ ] 生成中證據欄第一秒就在，沒有淡入或 stagger
- [ ] `degraded > 0` 時送出鈕上方有提示，但**不擋送出**
- [ ] **摘要工作台也有證據欄**（來源 Space／讀取則數／圖片／風格／模型），走同一個 `toEvidence({ origin: 'summary' })`
- [x] **元件與 hook 檔 < 250 行**（門檻於 2026-09-10 修訂，理由見 §9.1）：
  ```bash
  find /Users/cheng/google-chat-bot/dashboard/frontend/src -name '*.tsx' \
    -not -name '*.test.tsx' | xargs wc -l | sort -rn | head -5
  find /Users/cheng/google-chat-bot/dashboard/frontend/src/hooks -name '*.ts' \
    -not -name '*.test.ts' | xargs wc -l | sort -rn | head -3
  ```
  2026-09-10 實測最大是 `components/SpaceList.tsx` 243 與
  `hooks/useGlobalHotkeys.ts` 249，全部達成。
  `store/` 與 `lib/` 另計（§9.1 有理由與現況清單）
- [ ] 新增 `lib/evidence.test.ts`，四種 status 全覆蓋

### P5 — 色彩與排版退役

**做**：逐檔退役別名、字級重新指派、`.metric` 上場、刪 `scroll-area` 與 `tooltip`、復活 `skeleton` 與 `badge`、上守門測試、**刪掉 §3.4 的別名區塊**。

退役順序（血濺範圍由小到大）：`LoginScreen` → `ProviderSelect` → `SpaceList` → `ActionItems` → `CollectorPanel` → `App.tsx` → `SummaryWorkspace` → `MentionInbox` → `SpaceMessagePreview` → 草稿相關（P4 已重寫）。

映射是 1:1 機械的（`text-sky-*` → `text-signal` 等）；唯一需要眼睛的是**刪掉同一行的 `dark:` 孿生**。

**驗收**（全部用 grep 量，數字要對得上）
- [ ] 語意具名色 **107 → 0**
- [ ] 任意字級 `text-[…]` **118 → 0**（含 `ui/button.tsx:25` 那個 `text-[0.8rem]`——它不符合 `\d+px` 的樣式，是最容易漏掉的一個）
- [ ] `tabular-nums` **0 → 有**（所有計量都用 `.metric`）
- [ ] `title=` 只剩白名單
- [ ] JSX 文字中的 `·` → 0
- [ ] **整個 `src/` 的硬編碼 hex → 0**，含 `index.css` 裡 `.markdown-body` 與 `.typing-cursor` 的 4 處 `var(--color-sky-500, #0ea5e9)`（改成 `var(--signal)`，不留 fallback）
- [ ] `--sidebar-*` 與 `--chart-1~5` 已從 `index.css` 移除（`.tsx` 零使用）
- [ ] Toaster、Dialog、Select 的背景與圓角正常（相容層有效，見 §3.5）
- [ ] `@theme` 的別名區塊已刪除，且畫面無變化
- [ ] `SpaceMessagePreview` 的討論串色輪改成**單色系明度階梯 ＋ 序號**（見 §16）
- [ ] 建議回話 Textarea 不再是 `font-mono text-xs`

### P6 — 命令面板與鍵盤

**做**：`CommandPalette`、`useGlobalHotkeys`、roving tabindex 的虛擬清單導航、`SkipLink`、landmark、`keyboard.ts` 簽章擴充。

**驗收**
- [ ] **注音打字中按 Enter 不觸發任何全域快捷鍵**（實機測，不是只跑單元測試）
- [ ] `⌘K` 跳得到 436 個 Space 的任何一個
- [ ] 命令面板關閉後焦點回到觸發元素
- [ ] 「送出回話」「推播回 Google Chat」在命令面板搜不到
- [ ] 虛擬清單同時只有一個 `tabIndex=0`；`↓` 捲到底再按不會跳出清單
- [ ] `keyboard.test.ts` 已同步更新且通過

### P7 — 響應式與收尾

**做**：三個斷點、`SmallScreenNotice`、文案改寫（§13.2）、`api-contract.md` 修正（§13.3）。

**驗收**
- [ ] 1920 / 1280 / 1100 / 900 / 600 各截一次圖（淺深各一）
- [ ] 1024–1280 的抽屜開合狀態重新整理後保留，且與 1280 以上分開記憶
- [ ] <768 的說明頁與逃生門可用
- [ ] Lighthouse a11y 對 `#/mentions/:id` 與 `#/summary/:key` 各跑一次
- [ ] §13.2 每一列都改完
- [ ] `docs/api-contract.md:684-685` 已更新

---

## 15. 測試策略

### 15.1 既有 159 項的保護

**基準：159 項 / 11 檔，`463ms` 跑完（2026-09-09 實跑 `npm --prefix dashboard/frontend run test`）。**

| 檔案 | 項數 |
| :--- | ---: |
| `store/replySettings.test.ts` | 46 |
| `store/draft.test.ts` | 27 |
| `store/preview.test.ts` | 17 |
| `lib/merge.test.ts` | 14 |
| `store/providers.test.ts` | 11 |
| `lib/codeProjects.test.ts` | 9 |
| `lib/keyboard.test.ts` | 8 |
| `lib/aiErrors.test.ts` | 8 |
| `lib/sse.test.ts` | 7 |
| `store/mentions.test.ts` | 6 |
| `lib/actionItems.test.ts` | 6 |
| **合計** | **159** |

> `docs/HANDOFF.md` 寫的「前端 77 項」已經過時，不要拿它當門檻——用 77 當「數量不得減少」的基準等於這條門檻失效。動工前自己再跑一次取當下基準。

既有測試全部打在 store／lib 的純邏輯上，**零 DOM／className 斷言**，改版一項都不該失效。守法是 §17 的紅線 3（只准加、不准改，並用 `git diff` 檢查刪除行為 0）。

三個具體地雷：

| 動作 | 後果 |
| :--- | :--- |
| 把 `settingsSummary` 從 store 搬進元件 | 廢掉 `replySettings.test.ts` 一整組 |
| 為效能把 draft store 切 slice | 廢掉 `draft.test.ts` 一整組 |
| 改寫 `MERGE_BLOCK_LABEL` 文案 | `merge.test.ts` 有斷言，要同步 |

第三項的防護：把 `MERGE_BLOCK_LABEL` 搬到 `lib/mergeCopy.ts`，讓「改文案」與「改規則」變成兩個檔案的 diff，review 一眼分得出來。（這是**新增檔案**，`merge.ts` 只保留 re-export，淨改動仍在允許範圍。）

> **2026-09-10：已完成，但有一處與原文不同。** `lib/mergeCopy.ts` 建好了，
> 而 `merge.ts` **沒有**保留 re-export：`mergeCopy.ts` 要用 `merge.ts` 的
> `MERGE_MAX` 組「一次最多合併 N 則」，再從 `merge.ts` re-export 回去就形成
> 循環 import——`MERGE_MAX` 會落在 TDZ 裡，而它會不會炸取決於 bundler 有沒有
> 把那個 const 提前。那種「在 vitest 裡好、在某個建置設定下壞」的東西不值得留。
> 當時 `MERGE_BLOCK_LABEL` 只有一個呼叫端（`components/inbox/MentionCard.tsx`），
> 直接讓它改 import 更乾淨：「拿文案」與「拿規則」變成兩行看得出差別的 import，
> 而這正是這一項想達到的效果。
>
> 這一項是收尾盤點時才發現漏掉的——它只出現在 §15.1 與 §9.2 的目錄結構裡，
> 沒有進「實作進度」表，所以前三輪收尾都沒有人核對到。
> **規格裡的待辦不要只寫在正文，要同時進進度表。**

另外提醒：**合併回覆的規則有前後端兩份實作**（後端 `resolve_merge_targets`、前端 `lib/merge.ts`），改一邊要改兩邊。本次改版不動規則，只動文案。

### 15.2 新增純函式測試（node 環境，跟著既有 `include`）

`route.test.ts`、`evidence.test.ts`、`commands.test.ts`、`hotkeys.test.ts`、`listNavigation.test.ts`、`streamAnnouncements.test.ts`。

`vitest.config.ts` 現在是 `environment: 'node'`、`include: ['src/**/*.test.ts']`（連 `.tsx` 都不收）。這些都是 `.test.ts`，不必改設定。

### 15.3 守門測試 `src/lib/tokens.test.ts`（零相依，node 環境）

用 `node:fs` 掃 `src/**/*.tsx`，斷言五條：

1. 無 `(bg|text|border|ring|from|to|via|fill|stroke|divide|outline)-(sky|emerald|amber|violet|rose|teal|red|green|blue|yellow|slate|gray|zinc|neutral|stone)-\d{2,3}`
2. 無 `text-\[[0-9.]+(px|rem|em)\]`（任意字級全禁）。**不要只寫 `\d+px`**——`ui/button.tsx:25` 現在是 `text-[0.8rem]`，只比對 `\d+px` 的 pattern 會靜默放過它，然後回報「0 命中」讓人以為做完了
3. `title=` 的出現處必須全部落在**顯式白名單**裡；`<Tooltip` 一律零出現（`ui/tooltip.tsx` 已刪，這條是防止有人日後把它裝回來）
4. JSX 文字中無 `·`（U+00B7）
5. `index.css` 之外無硬編碼 hex

**這條測試同時是三件事**：P5 刪別名的憑據、本次改版核心約束的機械化守衛、以及防止下一個 feature 破窗的機制。

**第 3 條的白名單怎麼定**：用一份寫死在測試檔裡的 `檔案 → 允許次數` 表，每一筆附一句理由。改版完成後它只有一筆：

```ts
// 唯一允許的 title=：ConfirmDialog 的 title 是「對話框標題」這個 React prop，
// 不是 DOM 的 tooltip 屬性。新增任何一筆都要在 review 說明為什麼不能改成可見文字。
const TITLE_ALLOWLIST: Record<string, number> = {
  'components/draft/SendReplyConfirm.tsx': 1,
  'components/SummaryWorkspace.tsx': 1,
}
```

用「檔案 ＋ 次數」而不是行號，是因為行號會隨每次編輯漂移，維護不了；用次數則能擋住「偷偷多加一個」。**不要試圖用正則去分辨 DOM 屬性與元件 prop**——沒有 parser 的情況下分不乾淨，顯式白名單反而更誠實。

**寫完之後先讓它失敗一次。** 在還沒開始退役的檔案上跑，五條各自都要抓得到已知存在的違規（現況有 107 處具名色、118 處字級、29 處 title），數字對得上才代表 pattern 沒寫漏。**回報「0 命中」的把關工具本身要先有正對照**——`text-[0.8rem]` 就是被 `\d+px` 漏掉的實例。

### 15.4 元件測試（新增 jsdom project）

新增 devDependency：`@testing-library/react`、`@testing-library/user-event`、`@testing-library/jest-dom`、`jsdom`。

設定用 `test.projects`，讓 `*.test.tsx` 走 jsdom、`*.test.ts` 維持 node——**現有 159 項的執行環境完全不變，零回歸**。

必要性：本產品最貴的兩個錯誤是「送出了不可撤回的訊息」與「以為 Sepia 生效其實沒有」，兩者都發生在元件層的條件渲染，store 測試看不到。

只做四條高價值的：

1. **送出流程**：`meta.answering` 有 3 則時，確認框必須列出那 3 則的寄件人與時間；空白／串流中送出鈕 disabled；`send` 拋錯不關對話框。
2. **Sepia 三態**：`polished === false` 時 `fallback_reason` 必須用 `getByText` 找得到（**不是** `toHaveAttribute('title')`）。
3. **`EvidenceList`**：餵一份 `SseMeta` fixture，斷言每一項證據的文字都可被 `getByText` 找到——這就是「沒有東西只活在 title 裡」的行為式證明。
4. **命令面板**：`⌘K` 開啟／過濾／`↑↓`／`Enter`／`Esc` 還焦點；textarea 內 `⌘K` 要開但單按 `K` 不能開；**IME 組字中按 Enter 不得執行命令**。

每則元件測至少一條 `getByRole(role, { name })`，強迫可及名稱正確。

jsdom 沒有佈局，**不要去驗真實捲動位置**；虛擬清單只驗「同時只有一個 `tabIndex=0`」與「`↓` 呼叫 `scrollToIndex(期望 index)`」。

### 15.5 選配：`vitest-axe`

三個 smoke（已登入骨架／有草稿的工作區／開啟的設定頁）。axe 抓得到 landmark 與 label 缺失，**但抓不到「關鍵資訊藏在 title」**——那條靠 §15.3。

### 15.6 不引入 Playwright

既有 e2e 在 `tests/e2e/`（Python ＋ 少數 `.cjs`），前端改版不需要再加一套瀏覽器測試。視覺驗證用截圖人工比對（§16）。

---

## 16. 建置與驗證

### 16.1 每階段固定指令

```bash
npm --prefix dashboard/frontend run typecheck
npm --prefix dashboard/frontend run test        # 基準 159 項 / 11 檔，數量不得減少

# 建置＋蓋章（webapp.py 沒有 CLI 旗標，直接呼叫它的函式）
.venv/bin/python -c "import sys; sys.path.insert(0,'scripts'); import webapp; \
  print(webapp.build_frontend(webapp.find_npm()), webapp.frontend_state())"
# 期望輸出：True ready
```

### 16.2 建置：一律透過 `scripts/webapp.py`

> **警告**：`scripts/webapp.py:74-75` 的 `_SOURCE_FILES` 是 `index.html`、`package.json`、`vite.config.ts`、`tsconfig.json`、`components.json`，`:76` 的 `_SOURCE_DIRS` 是 `src`。改動其中任何一項都會讓 `source_hash()` 變動；而 `.buildinfo.json` **只有 `webapp.py` 會蓋章**（`:197`）。
>
> （`:77` 的 `_IGNORED_SUFFIXES` 會排除 `*.test.ts(x)` 與 `*.spec.ts(x)`，所以**只改測試檔不會讓 dist 變 stale**。）
>
> **單獨跑 `npm run build` 會留下舊 hash，dist 永遠被判定 stale、每次啟動都重建。**

`dashboard/frontend/dist/` **進版控**，每個 Phase 的 commit 要把它一起帶上。

### 16.3 視覺驗證

- 新增 `src/dev/TokenSheet.tsx`（僅開發路由 `#/dev/tokens`，正式建置不掛載）：把每個 token × 每種狀態 × 兩主題印在一頁。
- 在 1920 / 1280 / 1100 / 900 / 600 各截一次，淺深各一。
- 對 `#/mentions/:id` 與 `#/summary/:key` 跑 Lighthouse a11y。
- 用 DevTools 對比檢查器複驗 §10.8 的目標，特別是半透明疊色。

### 16.4 分支

從 `main` 開 `feature/ui-redesign-evidence-first`（`origin/HEAD → main`；專案慣例是 `feature/*` 分支再 `git merge --no-ff` 回 main）。

開完分支用 `git rev-parse HEAD` 與 `main` 比對確認基準點正確——這台機器的 `gitstatusd` 會製造 `index.lock` 殘留，`git checkout` 失敗後續指令仍會照跑。

---

## 17. 紅線（不可違反）

1. **後端零改動。** 需要的欄位後端都已支援；若發現真的缺，停下來回報，不要自己加。
2. **`CONTEXT.md` 的統一語言原樣保留。** 見 §13.1。
3. **既有 store／lib 只准「加」，不准「改」或「刪」。**
   - **可以**：新增 action、新增 state 欄位、新增 selector、開新檔（例：`codeProjects` 加 `loaded` 旗標、`mentions` 加 `startPolling`／`stopPolling`、在 `spaces.ts` 旁邊**新增** `sortByPinned` 而不動 `filterSpaces`）。
   - **不可以**：改動既有 export 的簽章或回傳形狀、把既有函式搬走、把 store 拆成 slice、改動既有測試斷言到的文案常數。
   - 唯一允許的簽章改動是 P6 的 `keyboard.ts` 的 `isComposing`（`keyboard.test.ts` 同步更新）。
   - 為既有 action 增加**可選參數**是允許的，但預設值必須讓既有呼叫端的行為
     **完全不變**（照 `store/replySettings.ts:198` 的 `async ({ force = false } = {})` 寫法）。
   - **機械檢查**：每個 Phase 收工對每個動過的既有 store／lib 檔跑
     `git diff -U0 <檔> | grep '^-' | grep -v '^---'`，逐行確認每一條刪除行
     都有對應的新版本（＝就地修改），**不得有淨移除**。
     注意不要寫成 `grep '^-[^-]'`——那會把原文以 `-` 開頭的內容行一起濾掉，變成靜默通過。
   - 最終的把關仍是 159 項測試零減少、零失敗。機械檢查只是讓「改了什麼」在
     review 時無所遁形。
4. **串流狀態繼續住在 store，切頁籤不中止生成。** `DraftReplyWorkspace.tsx:125-130` 與 `SummaryWorkspace.tsx:110-112` 的「刻意不在 unmount 時 abort」是踩過坑寫出來的，不得「順手修正」。
5. **`store/draft.ts:300-319` 的 `reset()` 不得清空跨 Mention 的偏好**（`referenceSpaceIds`／`codeRefs`／`refLimit`／`referenceSearch`／五個回覆設定欄位）。
6. **任何送出動作都要二次確認對話框**，且對話框必須顯示會送出的全文與目標。
7. **送出類動作不得有快捷鍵，也不得進命令面板。**
8. **不得用 tooltip 承載唯一資訊。** 由 §15.3 的守門測試機械性擋住。
9. **證據欄不得顯示任何無法回溯到 §5.1 表格某個欄位的東西。**
10. **`meta.answering` 才是送出時要結掉哪幾則的依據**（`store/draft.ts:113-122` 已實作），不得改用送出前的勾選。

---

## 18. 待實作時決定的一件小事

`SpaceMessagePreview.tsx:27-34` 的討論串色輪（sky/emerald/amber/violet/rose/teal 六色左邊框）與「全站一個訊號色」直接衝突。

改成**單色系明度階梯 ＋ 序號**（`①②③`…）：顏色在這裡的功能是「分群」不是「狀態」，用明度階梯同樣做得到，而且色盲友善。

若實作時發現六階明度在 3px 左邊框上分不出來，退回**四階明度 ＋ 序號**（序號本身就足以區分，明度只是加速掃視）。
