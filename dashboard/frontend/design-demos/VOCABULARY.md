# 表面分層的設計語彙

> 從**已經實作並驗證過**的摘要工作台提煉（2026-09-12）。
> Mention 收件匣、草稿回覆區、證據欄、設定中心要照這一套做，不要各長各的。
> 原始出處：`src/components/summary/SummaryOutput.tsx`、`src/components/ActionItems.tsx`、
> `src/components/summary/SummaryToolbar.tsx`。

## 1. 四個表面各代表什麼

| token | 語意 | 典型用法 |
|---|---|---|
| `bg-background` | 底。也是**凹進去的欄位** | 工作區的底、卡片內可勾選的項目 |
| `bg-surface` | 內容面 | 卡片本體 |
| `bg-raised` | **浮在內容之上的操作面** | 工具列、設定列 |
| `bg-muted` | 標籤底、hover 底 | chip、hover 狀態 |

**淺色的 ΔL(surface − background) 是 0.022，深色是 0.034。**兩個主題的階層策略不對稱，
那是刻意的（深色本來就夠，見 `docs/design/2026-09-12-admin-console.md` 第 3 節）。

⚠️ **凹陷欄位用 `bg-background` 不要用 `bg-muted`**：muted 在淺色比 surface 暗、
在深色比 surface 亮，拿它當「凹進去」兩個主題的方向會相反。background 在兩個主題
都比 surface 暗一階。

## 2. 卡片：標頭帶 ＋ 內容區

兩張已實作的卡片（摘要產出、Action Items）長得一樣，是為了讓「這一頁有幾個區塊」
用掃的就數得出來。新畫面照抄：

```
<section class="overflow-hidden rounded-xl border border-border bg-surface">
  <div class="flex items-center justify-between gap-2 border-b border-line px-4 py-2">
    ...標題（含計數）... ...動作按鈕...
  </div>
  <div class="p-4">  ← Markdown 類內容用 p-5
    ...內容...
  </div>
</section>
```

- 標頭用**同色 ＋ 一條細線**（`border-line`），**不要**用 muted 填色帶。
  選定的 v1 方向是輕卡片，階層靠邊框與一階填色差，不往上疊第三種底色。
- 卡片外框用 `border-border`（＝`--line`），卡片**內部**的分隔線也用 `border-line`。
- 卡片之間的間距：`space-y-4`。

## 3. 工具列

```
<div class="flex shrink-0 flex-wrap items-end gap-3 border-b border-border bg-raised px-5 py-3">
```

工具列是**操作面不是內容**，所以它浮起（raised）而不是變成卡片。
它橫跨整個工作區的寬度，貼在頂部，不要包進卡片裡。

## 4. 凹陷的項目（可勾選、可點選的列）

```
<label class="flex items-start gap-2.5 rounded-lg border border-line bg-background p-2
              transition-colors hover:border-line-strong">
```

用在 Action Items 的勾選列。Mention 清單的每一則、Reference Space 的每一個選項
都是同一種東西，照這個做。

## 5. chip 的三種

| 用途 | class |
|---|---|
| 當前選取／可互動（accent） | `rounded border border-signal-line bg-signal-wash px-2 py-0.5 font-medium text-signal` |
| 來源標記（中性） | `rounded border border-line bg-muted px-2 py-0.5 font-medium text-provenance` |
| 人名 | `rounded bg-signal-wash px-1.5 py-0.5 font-medium text-verified` |

## 6. 分隔符

**不用中點 `·`。**改用細豎線：

```
<span aria-hidden class="h-3 w-px bg-line-strong"></span>
```

理由：設計原則本來就禁用 `·`，而且它與等寬數字擠在一起時很難一眼切開欄位。
（`SpaceList` 那一側還沒改，那是既有漂移。）

## 7. 狀態：線型優先，顏色只是冗餘

| 線型 | 語意 |
|---|---|
| 實線 | 完整 |
| 虛線 `border-dashed` | 降級 |
| 點線 `border-dotted` | 缺值 |

色盲使用者不靠顏色也要讀得懂。左側導覽的「即將推出」就是用虛線群組框 ＋ 點線底線
表達的，證據欄的四種狀態也走同一套。

## 8. 不可違反

- **全站最大的字是 22px，而且只給計量值**（Token 用量那個數字）。標題不得比它大。
- 顏色是稀有資源：只有一個訊號色 ＋ 一個錯誤色。不要給每個功能一個顏色。
- **零漸層、零裝飾性陰影**。卡片階層靠填色差 ＋ 邊框，不是靠 box-shadow 疊灰。
  陰影只給真正的浮層（下拉、對話框、抽屜）。
- 等寬字只服務對齊（space id、model id、數值），**CJK 一律不用**。
- `title=` 不是呈現方式。要讓人知道的事寫在畫面上。
- 顏色一律走語意 token，**不准寫 hex、不准用 Tailwind 具名色 utility**
  （`bg-amber-600` 這類）——守門測試 `src/lib/tokens.test.ts` 會擋。
