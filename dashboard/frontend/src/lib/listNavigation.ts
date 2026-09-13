/**
 * 清單鍵盤導航：按鍵 → 下一個 index（設計規格 §10.5）。
 *
 * 抽成純函式的理由是它必須在 node 環境測得到：真正的清單是 436 筆的虛擬
 * 滾動，jsdom 沒有佈局、量不出捲動位置，把「按了 PageDown 該落在第幾列」
 * 綁在元件測試裡會變成測不準的東西。
 */

/** PageUp／PageDown 一次跳幾列。虛擬清單量不到「一頁幾列」，取固定值。 */
export const LIST_PAGE_SIZE = 10

/**
 * 回傳按下 `key` 之後應該落在哪一個 index；不是導航鍵、或清單是空的就回
 * `null`（呼叫端據此決定要不要 `preventDefault`）。
 *
 * **邊界一律夾住、不繞回去。** 436 筆的清單裡按住 ↓ 一路到底又跳回第一列，
 * 使用者會完全失去自己在哪的感覺；命令面板那種十來筆的清單才適合繞回。
 */
export function nextListIndex(
  key: string,
  current: number,
  count: number,
  pageSize: number = LIST_PAGE_SIZE,
): number | null {
  if (count <= 0) return null

  // current 可能來自上一輪的搜尋結果而超出範圍，先夾回合法區間再算
  const from = Math.min(Math.max(current, 0), count - 1)
  const clamp = (index: number) => Math.min(Math.max(index, 0), count - 1)

  switch (key) {
    case 'ArrowDown':
      return clamp(from + 1)
    case 'ArrowUp':
      return clamp(from - 1)
    case 'Home':
      return 0
    case 'End':
      return count - 1
    case 'PageDown':
      return clamp(from + pageSize)
    case 'PageUp':
      return clamp(from - pageSize)
    default:
      return null
  }
}

/**
 * 在一份清單裡從 `currentId` 往前／往後找一格，回傳那一項的 id
 * （規格 §11.1 的 `[`／`]`：上一則／下一則 Mention）。
 *
 * 三個邊界情況都刻意回 `null`，讓呼叫端可以「什麼都不做」而不是亂跳：
 * - 清單是空的
 * - 目前這一則不在清單裡（例如它是摘要工作台挑的 `manual`，而現在看的是
 *   已處理分頁；或者剛被標成已處理、從待處理分頁消失了）
 * - 已經在頭或尾——**不繞回去**。與 `nextListIndex` 同一個理由：繞回讓人
 *   失去「我在哪」的感覺，而這裡連清單都不一定看得見（在草稿工作區按
 *   `[`／`]` 時左欄可能捲到別處），繞回會更莫名其妙。
 */
export function stepId<T extends { id: number }>(
  items: T[],
  currentId: number | null,
  delta: 1 | -1,
): number | null {
  if (items.length === 0) return null

  // 還沒選任何一則：`]` 給第一則、`[` 給最後一則，讓這兩個鍵在空手時
  // 也是一個入口，而不是完全沒反應
  if (currentId === null) return (delta === 1 ? items[0] : items[items.length - 1]).id

  const at = items.findIndex((item) => item.id === currentId)
  if (at < 0) return null

  const next = at + delta
  if (next < 0 || next >= items.length) return null
  return items[next].id
}
