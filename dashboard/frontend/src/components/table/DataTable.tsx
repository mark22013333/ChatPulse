import { useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from 'react'
import { ArrowDownIcon, ArrowUpDownIcon, ArrowUpIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { nextListIndex } from '@/lib/listNavigation'
import { ariaSortOf, type SortState } from '@/lib/paginate'

/**
 * 共用表格（設計規格 §4.2，S3 摘要工作台與 S4 工時草稿共用）。
 *
 * 🔴 這個元件**只畫傳進來的那幾列**，不篩選、不排序、不切頁。那三件事一律
 * 先在 `lib/paginate.ts` 對全部資料做完——元件若自己再排一次，排的就只是
 * 當頁那 25 筆，畫面看起來排好了、實際少了其他頁，而且不會報錯。
 * 表頭的排序按鈕只負責「告訴呼叫端 Viewer 點了哪一欄」。
 *
 * 語意用真的 `<table>`／`<th scope="col">`，不是 div 格線：讀屏靠它唸出
 * 「第 3 列，最後活動欄，10:42」，div 做不到。
 */

export interface DataTableColumn<T, K extends string = string> {
  id: string
  header: ReactNode
  /** 有值＝這欄可排序，表頭變成按鈕並帶 `aria-sort` */
  sortKey?: K
  cell: (row: T) => ReactNode
  /** 數字欄：靠右、等寬數字（`.metric`），上下列的位數才對得齊 */
  numeric?: boolean
  className?: string
  headerClassName?: string
}

export interface DataTableProps<T, K extends string = string> {
  /** 表格的無障礙名稱（畫成 sr-only 的 `<caption>`） */
  caption: string
  columns: DataTableColumn<T, K>[]
  /** 🔴 **當頁**的列，已經篩選、排序、切頁過（`paginate().items`） */
  rows: readonly T[]
  rowKey: (row: T) => string | number
  sort?: SortState<K> | null
  onSortChange?: (key: K) => void
  /** 點一列、或在列上按 Enter */
  onRowActivate?: (row: T) => void
  /** 0 列時顯示的內容 */
  empty?: ReactNode
  className?: string
}

/**
 * 列裡面自己就會處理點擊的元素（例：摘要工作台的 ★ 釘選按鈕）。點到它們時
 * **不**觸發整列的 onRowActivate——否則點 ★ 釘選的同時會整頁切到詳情。
 * 呼叫端不必記得每個按鈕都要 stopPropagation。
 */
const INTERACTIVE = 'button, a, input, select, textarea, label, [role="checkbox"], [role="switch"]'

function fromInnerControl(event: { target: EventTarget; currentTarget: Element }): boolean {
  const target = event.target as Element | null
  const hit = target?.closest?.(INTERACTIVE)
  return !!hit && hit !== event.currentTarget && event.currentTarget.contains(hit)
}

/**
 * ↑↓ 只在當頁內移動（規格 §4.2）。**不處理 PageUp／PageDown**：那兩個鍵
 * 在表格裡最自然的意思是「翻頁」，但規格否決了「走到頁尾自動翻頁」
 * （焦點會突然跳走），給它一個「在頁內跳 10 列」的意思只會讓人更困惑。
 */
const ROW_KEYS = new Set(['ArrowDown', 'ArrowUp', 'Home', 'End'])

export function DataTable<T, K extends string = string>({
  caption,
  columns,
  rows,
  rowKey,
  sort = null,
  onSortChange,
  onRowActivate,
  empty,
  className,
}: DataTableProps<T, K>) {
  // roving tabindex：整張表只有一列在 Tab 序裡，其餘靠 ↑↓ 走。
  // 否則 25 列就是 25 個 Tab 站，Viewer 要按 25 下才走得到分頁器
  const [active, setActive] = useState(0)
  const bodyRef = useRef<HTMLTableSectionElement>(null)
  // 換頁、篩選之後列數可能變少，上一輪的 active 要夾回來，否則沒有任何一列可以 Tab 進來
  const tabbable = rows.length === 0 ? -1 : Math.min(active, rows.length - 1)

  function focusRow(index: number) {
    setActive(index)
    const row = bodyRef.current?.querySelectorAll<HTMLTableRowElement>(':scope > tr')[index]
    row?.focus()
  }

  function onRowKeyDown(event: KeyboardEvent<HTMLTableRowElement>, index: number, row: T) {
    // 焦點在列裡面的按鈕上時，按鍵是那個按鈕的（Enter 要按下 ★，不是開詳情）
    if (event.target !== event.currentTarget) return
    if (event.key === 'Enter') {
      event.preventDefault()
      onRowActivate?.(row)
      return
    }
    if (!ROW_KEYS.has(event.key)) return
    const next = nextListIndex(event.key, index, rows.length)
    if (next === null) return
    event.preventDefault()
    focusRow(next)
  }

  function onRowClick(event: MouseEvent<HTMLTableRowElement>, index: number, row: T) {
    if (fromInnerControl(event)) return
    setActive(index)
    onRowActivate?.(row)
  }

  return (
    <div className={cn('overflow-x-auto rounded-xl border border-line bg-surface', className)}>
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => {
              const ariaSort = column.sortKey ? ariaSortOf(sort, column.sortKey) : undefined
              return (
                <th
                  key={column.id}
                  scope="col"
                  aria-sort={ariaSort}
                  className={cn(
                    'border-b border-line px-3 py-2.5 text-left text-xs font-semibold whitespace-nowrap text-fg-dim',
                    column.numeric && 'text-right',
                    column.headerClassName,
                  )}
                >
                  {column.sortKey ? (
                    <SortButton
                      label={column.header}
                      state={ariaSort}
                      numeric={column.numeric}
                      onClick={() => onSortChange?.(column.sortKey as K)}
                    />
                  ) : (
                    column.header
                  )}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody ref={bodyRef}>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-3 py-8 text-center text-fg-dim">
                {empty ?? '沒有符合條件的資料'}
              </td>
            </tr>
          ) : (
            rows.map((row, index) => (
              <tr
                key={rowKey(row)}
                tabIndex={index === tabbable ? 0 : -1}
                onClick={(event) => onRowClick(event, index, row)}
                onKeyDown={(event) => onRowKeyDown(event, index, row)}
                // 焦點靠 Tab 或點擊移進來時同步 active，否則接著按 ↓ 會從別的列開始走
                onFocus={(event) => {
                  if (event.target === event.currentTarget) setActive(index)
                }}
                className={cn(
                  'border-b border-line transition-colors last:border-b-0',
                  onRowActivate && 'cursor-pointer hover:bg-signal-wash',
                )}
              >
                {columns.map((column) => (
                  <td
                    key={column.id}
                    className={cn(
                      'px-3 py-2.5 align-middle',
                      column.numeric && 'metric text-right',
                      column.className,
                    )}
                  >
                    {column.cell(row)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}

function SortButton({
  label,
  state,
  numeric,
  onClick,
}: {
  label: ReactNode
  state: 'ascending' | 'descending' | undefined
  numeric?: boolean
  onClick: () => void
}) {
  // 圖示只是視覺提示，排序狀態由 th 的 aria-sort 承擔；圖示再唸一次會重複
  const Icon = state === 'ascending' ? ArrowUpIcon : state === 'descending' ? ArrowDownIcon : ArrowUpDownIcon
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'inline-flex items-center gap-1 rounded-sm hover:text-foreground',
        state && 'text-foreground',
        numeric && 'flex-row-reverse',
      )}
    >
      {label}
      <Icon aria-hidden className={cn('size-3', !state && 'opacity-50')} />
    </button>
  )
}
