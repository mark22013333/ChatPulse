import { useCallback, useMemo, useState } from 'react'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DataTable, type DataTableColumn } from './DataTable'
import { Pager, TableTotal } from './Pager'
import { usePagedRows } from './usePagedRows'
import { toggleSort, type SortState } from '@/lib/paginate'
import { resetPageSizeForTest } from '@/store/pageSize'
import { useAuthStore } from '@/store/auth'
import type { Me } from '@/lib/types'

interface Row {
  id: number
  name: string
  count: number
  kind: 'a' | 'b'
}

type Key = 'name' | 'count'

/** 30 列：count 與 id 同向，所以傳入順序下 count 最大的第 30 列只在第二頁 */
const ROWS: Row[] = Array.from({ length: 30 }, (_, i) => ({
  id: i + 1,
  name: `列 ${String(i + 1).padStart(2, '0')}`,
  count: i + 1,
  kind: i + 1 >= 28 ? 'b' : 'a',
}))

const COLUMNS: DataTableColumn<Row, Key>[] = [
  { id: 'name', header: '名稱', sortKey: 'name', cell: (r) => r.name },
  { id: 'count', header: '數量', sortKey: 'count', numeric: true, cell: (r) => r.count },
  {
    id: 'pin',
    header: '釘選',
    cell: (r) => (
      <button type="button" onClick={() => pinned.push(r.id)}>
        釘選 {r.id}
      </button>
    ),
  },
]

let pinned: number[] = []

/**
 * 把三件事接起來的最小頁面：篩選框、表格、分頁器、共 N 筆。
 * S3／S4 會照這個形狀接，這裡等於先替它們驗一次整條管線。
 */
function Harness({ onActivate }: { onActivate?: (row: Row) => void }) {
  const [onlyB, setOnlyB] = useState(false)
  const [sort, setSort] = useState<SortState<Key> | null>(null)
  const filter = useCallback((r: Row) => (onlyB ? r.kind === 'b' : true), [onlyB])
  const sorter = useMemo(() => {
    if (!sort) return undefined
    const dir = sort.direction === 'asc' ? 1 : -1
    return (xs: readonly Row[]) =>
      [...xs].sort((a, b) =>
        sort.key === 'count' ? (a.count - b.count) * dir : a.name.localeCompare(b.name) * dir,
      )
  }, [sort])
  const paged = usePagedRows({
    items: ROWS,
    filter,
    sort: sorter,
    resetKey: String(onlyB),
    table: 'summary',
  })
  return (
    <div>
      <label>
        <input type="checkbox" checked={onlyB} onChange={(e) => setOnlyB(e.target.checked)} />
        只看 b
      </label>
      <TableTotal total={paged.total} />
      <DataTable
        caption="測試表格"
        columns={COLUMNS}
        rows={paged.items}
        rowKey={(r) => r.id}
        sort={sort}
        onSortChange={(key) => setSort((s) => toggleSort(s, key, key === 'count' ? 'desc' : 'asc'))}
        onRowActivate={onActivate}
      />
      <Pager
        page={paged.page}
        pageCount={paged.pageCount}
        pageSize={paged.pageSize}
        onPageChange={paged.setPage}
        onPageSizeChange={paged.setPageSize}
      />
    </div>
  )
}

function fakeStorage() {
  const data = new Map<string, string>()
  return {
    data,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    clear: () => data.clear(),
    key: () => null,
    length: 0,
  }
}

let storage: ReturnType<typeof fakeStorage>

beforeEach(() => {
  pinned = []
  storage = fakeStorage()
  vi.stubGlobal('localStorage', storage)
  resetPageSizeForTest()
  useAuthStore.setState({ me: { viewer: { id: 5 } } as unknown as Me })
})

afterEach(() => {
  vi.unstubAllGlobals()
  useAuthStore.setState({ me: null })
})

const bodyRows = () => within(screen.getAllByRole('rowgroup')[1]).getAllByRole('row')
const firstCellTexts = () => bodyRows().map((row) => within(row).getAllByRole('cell')[0].textContent)

describe('表格語意', () => {
  it('<table>、caption、th scope=col；可排序欄是按鈕，只有正在排的那欄有 aria-sort', async () => {
    render(<Harness />)
    const table = screen.getByRole('table', { name: '測試表格' })
    const headers = within(table).getAllByRole('columnheader')
    expect(headers.map((h) => h.getAttribute('scope'))).toEqual(['col', 'col', 'col'])
    expect(within(headers[0]).getByRole('button', { name: '名稱' })).toBeInTheDocument()
    expect(within(headers[2]).queryByRole('button')).toBeNull()
    expect(headers.some((h) => h.hasAttribute('aria-sort'))).toBe(false)

    await userEvent.click(within(headers[1]).getByRole('button', { name: '數量' }))
    expect(headers[1]).toHaveAttribute('aria-sort', 'descending')
    expect(headers[0]).not.toHaveAttribute('aria-sort')
    await userEvent.click(within(headers[1]).getByRole('button', { name: '數量' }))
    expect(headers[1]).toHaveAttribute('aria-sort', 'ascending')
  })
})

describe('🔴 排序與篩選作用在全部資料上（第二頁資料當正對照，元件層）', () => {
  it('點「數量」排大→小：只在第二頁的第 30 列跑到第一頁第一列', async () => {
    render(<Harness />)
    // 前提：預設第一頁看不到第 30 列
    expect(firstCellTexts()).not.toContain('列 30')
    expect(bodyRows()).toHaveLength(25)
    await userEvent.click(screen.getByRole('button', { name: '數量' }))
    expect(firstCellTexts()[0]).toBe('列 30')
  })

  it('篩選：符合的只有第二頁的三列 → 第一頁就看得到，「共 N 筆」是篩選後總數', async () => {
    render(<Harness />)
    expect(screen.getByText(/共/).textContent).toBe('共 30 筆')
    await userEvent.click(screen.getByRole('checkbox', { name: '只看 b' }))
    expect(firstCellTexts()).toEqual(['列 28', '列 29', '列 30'])
    expect(screen.getByText(/共/).textContent).toBe('共 3 筆')
  })
})

describe('頁碼', () => {
  it('篩選條件改變 → 回到第 1 頁', async () => {
    render(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: '第 2 頁' }))
    expect(screen.getByRole('button', { name: '第 2 頁' })).toHaveAttribute('aria-current', 'page')
    expect(firstCellTexts()[0]).toBe('列 26')

    await userEvent.click(screen.getByRole('checkbox', { name: '只看 b' }))
    expect(screen.getByRole('button', { name: '第 1 頁' })).toHaveAttribute('aria-current', 'page')
    await userEvent.click(screen.getByRole('checkbox', { name: '只看 b' }))
    // 取消篩選也算條件改變：還是第 1 頁，不是回到先前的第 2 頁
    expect(screen.getByRole('button', { name: '第 1 頁' })).toHaveAttribute('aria-current', 'page')
    expect(firstCellTexts()[0]).toBe('列 01')
  })

  it('上一頁／下一頁在頭尾停用', async () => {
    render(<Harness />)
    expect(screen.getByRole('button', { name: '上一頁' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: '下一頁' }))
    expect(screen.getByRole('button', { name: '下一頁' })).toBeDisabled()
    expect(firstCellTexts()).toEqual(['列 26', '列 27', '列 28', '列 29', '列 30'])
  })

  it('換每頁筆數：寫進偏好，而且目前這頁的第一列仍在畫面上', async () => {
    render(<Harness />)
    // 每頁 25 → 10：先到第 3 頁（每頁 10 筆），第一列是列 21
    await pickPageSize('每頁 10 筆')
    expect(bodyRows()).toHaveLength(10)
    await userEvent.click(screen.getByRole('button', { name: '第 3 頁' }))
    expect(firstCellTexts()[0]).toBe('列 21')
    await pickPageSize('每頁 25 筆')
    // offset 20 在每頁 25 筆時落在第 1 頁（列 01～25），列 21 仍看得到
    expect(firstCellTexts()).toContain('列 21')
    expect(JSON.parse(storage.data.get('chatpulse.pageSize.5') ?? '{}')).toEqual({ summary: 25 })
  })

  it('每頁筆數重新整理後保留（重新掛載、清掉記憶體快取）', async () => {
    const { unmount } = render(<Harness />)
    await pickPageSize('每頁 10 筆')
    unmount()
    act(() => resetPageSizeForTest())
    render(<Harness />)
    expect(bodyRows()).toHaveLength(10)
    expect(screen.getByRole('combobox', { name: '每頁筆數' })).toHaveTextContent('每頁 10 筆')
  })
})

describe('列的操作', () => {
  it('點一列 → onRowActivate；點列裡的按鈕不會觸發整列', async () => {
    const onActivate = vi.fn()
    render(<Harness onActivate={onActivate} />)
    await userEvent.click(bodyRows()[2])
    expect(onActivate).toHaveBeenLastCalledWith(ROWS[2])
    onActivate.mockClear()
    await userEvent.click(screen.getByRole('button', { name: '釘選 4' }))
    expect(pinned).toEqual([4])
    expect(onActivate).not.toHaveBeenCalled()
  })

  it('↑↓ 在當頁內移動、到底夾住不翻頁；Enter 開啟；整張表只有一個 Tab 站', async () => {
    const onActivate = vi.fn()
    render(<Harness onActivate={onActivate} />)
    const rows = bodyRows()
    expect(rows.filter((r) => r.tabIndex === 0)).toHaveLength(1)

    rows[0].focus()
    await userEvent.keyboard('{ArrowDown}{ArrowDown}')
    expect(document.activeElement).toBe(rows[2])
    await userEvent.keyboard('{Enter}')
    expect(onActivate).toHaveBeenLastCalledWith(ROWS[2])

    await userEvent.keyboard('{End}')
    expect(document.activeElement).toBe(rows[24])
    await userEvent.keyboard('{ArrowDown}')
    // 🔴 不自動翻到第二頁（規格否決：焦點會突然跳走）
    expect(document.activeElement).toBe(rows[24])
    expect(screen.getByRole('button', { name: '第 1 頁' })).toHaveAttribute('aria-current', 'page')
    await userEvent.keyboard('{ArrowUp}')
    expect(document.activeElement).toBe(rows[23])
  })

  it('焦點在列裡的按鈕上時，Enter 是那個按鈕的，不開啟整列', async () => {
    const onActivate = vi.fn()
    render(<Harness onActivate={onActivate} />)
    screen.getByRole('button', { name: '釘選 1' }).focus()
    await userEvent.keyboard('{Enter}')
    expect(pinned).toEqual([1])
    expect(onActivate).not.toHaveBeenCalled()
  })

  it('0 列時顯示空狀態，而不是一張沒有列的表', () => {
    render(<DataTable caption="空" columns={COLUMNS} rows={[]} rowKey={(r) => r.id} empty="沒有資料" />)
    expect(screen.getByText('沒有資料')).toBeInTheDocument()
  })
})

async function pickPageSize(label: string) {
  await userEvent.click(screen.getByRole('combobox', { name: '每頁筆數' }))
  const listbox = await screen.findByRole('listbox')
  await userEvent.click(within(listbox).getByText(label))
}
