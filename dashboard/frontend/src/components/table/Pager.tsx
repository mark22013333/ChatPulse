import { ChevronLeftIcon, ChevronRightIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { PAGE_SIZES, pageTokens, parsePageSize, type PageSize } from '@/lib/paginate'

/**
 * 表格的分頁器：上一頁、頁碼、下一頁，右側選每頁筆數（設計規格 §4.2）。
 *
 * 只負責呈現與回報 Viewer 點了什麼；頁碼的夾回、篩選後回第 1 頁都在
 * `usePagedRows`／`lib/paginate.ts`，這裡拿到的 `page` 已經是合法值。
 */

export interface PagerProps {
  /** 目前頁碼，從 1 起算 */
  page: number
  pageCount: number
  pageSize: PageSize
  onPageChange: (page: number) => void
  onPageSizeChange: (size: PageSize) => void
  /** `<nav>` 的無障礙名稱；一頁上有兩張表時要分得出是哪一張的分頁 */
  label?: string
  className?: string
}

const SIZE_ITEMS = PAGE_SIZES.map((size) => ({ value: String(size), label: `每頁 ${size} 筆` }))

const cell =
  'inline-flex h-8 min-w-8 items-center justify-center border border-line bg-surface px-2 text-sm -ml-px first:ml-0 first:rounded-l-lg last:rounded-r-lg'

export function Pager({
  page,
  pageCount,
  pageSize,
  onPageChange,
  onPageSizeChange,
  label = '分頁',
  className,
}: PagerProps) {
  const tokens = pageTokens(page, pageCount)
  const atFirst = page <= 1
  const atLast = page >= pageCount

  return (
    <div className={cn('flex flex-wrap items-center gap-3', className)}>
      <nav aria-label={label}>
        <ul className="flex items-center">
          <li className="contents">
            <button
              type="button"
              className={cn(cell, 'disabled:text-disabled-fg')}
              aria-label="上一頁"
              disabled={atFirst}
              onClick={() => onPageChange(page - 1)}
            >
              <ChevronLeftIcon aria-hidden className="size-4" />
            </button>
          </li>
          {tokens.map((token, index) =>
            token === 'gap' ? (
              // 省略號不是按鈕；位置 index 當 key，因為同一列可能有兩個 gap
              <li key={`gap-${index}`} className="contents">
                <span aria-hidden className={cn(cell, 'text-fg-dim')}>
                  …
                </span>
              </li>
            ) : (
              <li key={token} className="contents">
                <button
                  type="button"
                  className={cn(
                    cell,
                    'metric',
                    token === page
                      ? 'relative border-signal bg-signal text-signal-on'
                      : 'hover:bg-signal-wash',
                  )}
                  aria-label={`第 ${token} 頁`}
                  aria-current={token === page ? 'page' : undefined}
                  onClick={() => onPageChange(token)}
                >
                  {token}
                </button>
              </li>
            ),
          )}
          <li className="contents">
            <button
              type="button"
              className={cn(cell, 'disabled:text-disabled-fg')}
              aria-label="下一頁"
              disabled={atLast}
              onClick={() => onPageChange(page + 1)}
            >
              <ChevronRightIcon aria-hidden className="size-4" />
            </button>
          </li>
        </ul>
      </nav>

      <Select
        items={SIZE_ITEMS}
        value={String(pageSize)}
        onValueChange={(next) => {
          // ⚠️ Base UI 的 Select 清除選擇時給 null；Number(null) 是 0，
          // 一頁 0 筆等於整張表消失。parsePageSize 認不得就什麼都不做
          const size = parsePageSize(next)
          if (size !== null) onPageSizeChange(size)
        }}
      >
        <SelectTrigger size="sm" className="ml-auto" aria-label="每頁筆數">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {SIZE_ITEMS.map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

/**
 * 篩選列右側的「共 N 筆」。
 *
 * 🔴 `total` 必須是**篩選後的總數**（`paginate().total`），不是當頁筆數——
 * 每頁 25 筆的表永遠顯示「共 25 筆」，Viewer 就不會知道後面還有。
 * `aria-live` 讓讀屏在篩選改變後唸出新的數字，否則打完字不知道有沒有篩到東西。
 */
export function TableTotal({
  total,
  unit = '筆',
  className,
}: {
  total: number
  /** 例：「個 Space」→「共 23 個 Space」 */
  unit?: string
  className?: string
}) {
  return (
    <span aria-live="polite" className={cn('text-xs text-fg-dim', className)}>
      共 <span className="metric">{total}</span> {unit}
    </span>
  )
}
