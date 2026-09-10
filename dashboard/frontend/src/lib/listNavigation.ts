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
