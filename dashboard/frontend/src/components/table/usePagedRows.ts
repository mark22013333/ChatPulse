import { useMemo, useState } from 'react'
import { paginate, type PageResult, type PageSize } from '@/lib/paginate'
import { useViewerPageSize, type TableId } from '@/store/pageSize'

/**
 * 把「全部資料 ＋ 篩選 ＋ 排序」接成表格要的當頁資料，並管理頁碼與每頁筆數。
 *
 * 頁碼是元件 state，不進 store：規格 §4.1 的「返回時頁碼還在」靠的是表格
 * 收起來時**不卸載**（inert ＋ 透明），不是靠記憶；放進 store 反而會讓
 * 重新整理之後停在第 7 頁、而資料已經只剩 3 頁。每頁筆數才是偏好，進 store。
 *
 * ⚠️ `filter` 與 `sort` 請用 useCallback／useMemo 穩住，否則每次 render 都重算
 * 整份篩選排序（結果仍然正確，只是白做工）。
 */

export interface UsePagedRowsOptions<T> {
  items: readonly T[]
  filter?: (item: T) => boolean
  sort?: (items: readonly T[]) => readonly T[]
  /**
   * 篩選條件的指紋（例：`JSON.stringify(filters)`）。**它一變，頁碼回到第 1 頁**。
   *
   * 為什麼要呼叫端給、不自己比 `filter` 函式：函式每次 render 都可能是新的
   * 參考，拿它當判準的話 Viewer 在第 3 頁時任何一次重繪都會把他踢回第 1 頁。
   * 排序要不要也回第 1 頁由呼叫端決定——要的話把排序狀態也放進來。
   */
  resetKey: string
  table: TableId
}

export interface PagedRows<T> extends PageResult<T> {
  pageSize: PageSize
  setPage: (page: number) => void
  setPageSize: (size: PageSize) => void
}

export function usePagedRows<T>({
  items,
  filter,
  sort,
  resetKey,
  table,
}: UsePagedRowsOptions<T>): PagedRows<T> {
  const { pageSize, setPageSize: savePageSize } = useViewerPageSize(table)
  const [page, setPage] = useState(1)
  const [seenKey, setSeenKey] = useState(resetKey)

  // 篩選一變就回第 1 頁。寫在 render 裡而不是 useEffect：effect 版會先用舊頁碼
  // 畫一次新的篩選結果（可能是空白頁或被夾到最後一頁），下一格才跳回第 1 頁，
  // 看得到閃一下。這是 React 文件認可的「依 props 調整 state」寫法。
  let wanted = page
  if (seenKey !== resetKey) {
    setSeenKey(resetKey)
    setPage(1)
    wanted = 1
  }

  const result = useMemo(
    () => paginate(items, { filter, sort, page: wanted, pageSize }),
    [items, filter, sort, wanted, pageSize],
  )

  // 超出總頁數時 paginate 已經夾回最後一頁；state 也跟著改成夾回後的值，
  // 否則資料之後變多時，畫面會莫名其妙從第 3 頁跳回 Viewer 早就離開的第 7 頁
  if (result.page !== wanted) setPage(result.page)

  return {
    ...result,
    pageSize,
    setPage,
    setPageSize: (size) => {
      // 換筆數時讓「目前這頁的第一列」仍在畫面上，而不是把人丟回第 1 頁：
      // 在第 3 頁（第 51～75 列）改成每頁 50 筆 → 落在第 2 頁（第 51～100 列）
      setPage(Math.floor(result.offset / size) + 1)
      savePageSize(size)
    },
  }
}
