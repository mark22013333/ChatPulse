import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { WorklogEntryCard } from '@/components/worklog/WorklogEntryCard'
import { entryStateText, type WorklogEntryPatch } from '@/store/worklogDay'
import type { WorklogCandidate, WorklogDayEntry, WorklogEntryState } from '@/lib/types'

/**
 * 一張草稿卡（AC-3、D-16、AC-10）。
 *
 * 守的是三件「畫面看起來沒事」的壞法：
 *
 * 1. **歸屬預選**：候選排第一的那個被當成已選，Viewer 一路按送出就送了系統猜的專案
 *    （ADR-0008 的唯一硬規則）。
 * 2. **存檔多帶欄位**：只改時數卻把整筆帶出去，或 Enter 之後 blur 再存一次。
 * 3. **已送出的筆還能改**：後端會 409，但畫面讓人以為改得動。
 *
 * 斷言用 Viewer 看得到的東西（可及名稱、role）加上 `onSave` 實際收到的 patch——
 * 後者才是這張卡存在的目的。🔴 不斷言文案措辭。
 */

function candidate(over: Partial<WorklogCandidate> = {}): WorklogCandidate {
  return {
    issue_id: 501,
    issue_key: 'PEI-133',
    title: '匯出逾時',
    project_id: 42,
    project_key: 'PEI',
    source: 'issue_key',
    confidence: 'strong',
    reason: 'commit 訊息帶了 issue key',
    can_worklog: true,
    permission_checked: true,
    permission_reason: '',
    ...over,
  }
}

const CANDIDATES = [
  candidate(),
  candidate({ issue_id: 502, issue_key: 'PEI-140', title: '設定頁', project_id: 43, project_key: 'OPS', confidence: 'weak' }),
]

function entry(over: Partial<WorklogDayEntry> = {}): WorklogDayEntry {
  return {
    id: 11,
    draft_id: 1,
    seq: 1,
    cluster_key: 'c-a',
    label: '匯出逾時',
    reason: '同一個 issue key',
    issue_id: null,
    issue_key: null,
    project_id: null,
    project_key: null,
    hours: '2.00',
    note: '修正匯出逾時',
    allocated_hours: '2.00',
    weight: 1,
    rounded_up: false,
    evidence_refs: ['commit:aaa'],
    candidates: CANDIDATES,
    state: 'pending',
    zplanner_worklog_id: null,
    zplanner_issue_key: null,
    is_locked: false,
    submitted_at: null,
    last_error_code: null,
    last_error_message: null,
    created_at: '2026-09-13T00:00:00+00:00',
    updated_at: '2026-09-13T00:00:00+00:00',
    ...over,
  }
}

function mount(e: WorklogDayEntry = entry(), extra: { error?: string | null; saving?: boolean } = {}) {
  const onSave = vi.fn(async (_id: number, _patch: WorklogEntryPatch) => true)
  const onDirtyChange = vi.fn()
  const view = render(<WorklogEntryCard entry={e} onSave={onSave} onDirtyChange={onDirtyChange} {...extra} />)
  return { onSave, onDirtyChange, view }
}

const trigger = () => screen.getByRole('combobox', { name: '專案歸屬' })
const hoursInput = () => screen.getByLabelText('時數') as HTMLInputElement
const noteInput = () => screen.getByLabelText('備註') as HTMLTextAreaElement

async function pickOption(optionText: string | RegExp) {
  await userEvent.click(trigger())
  const listbox = await screen.findByRole('listbox')
  await userEvent.click(within(listbox).getByText(optionText))
}

describe('WorklogEntryCard：歸屬一律從未指派開始（AC-3、ADR-0008）', () => {
  it('🔴 產生後未指派：Select 顯示「未指派」，不是排第一的候選', () => {
    mount()
    expect(trigger()).toHaveTextContent('未指派')
    expect(trigger()).not.toHaveTextContent('PEI-133')
    expect(trigger()).not.toHaveTextContent('PEI-140')
  })

  it('正對照：已選的 entry 顯示它選的那個', () => {
    mount(entry({ issue_id: 502, issue_key: 'PEI-140', project_id: 43, project_key: 'OPS' }))
    expect(trigger()).toHaveTextContent('PEI-140')
    expect(trigger()).not.toHaveTextContent('未指派')
  })

  it('目前的選擇不在候選裡時照實顯示，不假裝成未指派', () => {
    mount(entry({ issue_id: 777, issue_key: 'OPS-9', candidates: CANDIDATES }))
    expect(trigger()).toHaveTextContent('OPS-9')
    expect(trigger()).not.toHaveTextContent('未指派')
  })

  it('選一個候選 → 立刻存，四欄一起帶（D-12），不帶時數與備註', async () => {
    const { onSave } = mount()
    await pickOption(/PEI-140/)
    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith(11, { issue_id: 502, issue_key: 'PEI-140', project_id: 43, project_key: 'OPS' })
  })

  it('從已選改回「未指派」→ 四欄全 null，不是 0', async () => {
    const { onSave } = mount(entry({ issue_id: 501, issue_key: 'PEI-133', project_id: 42, project_key: 'PEI' }))
    await pickOption('未指派')
    expect(onSave).toHaveBeenCalledWith(11, { issue_id: null, issue_key: null, project_id: null, project_key: null })
    const sent = onSave.mock.calls[0][1] as Record<string, unknown>
    expect(Object.values(sent).some((v) => v === 0)).toBe(false)
  })

  // 哨兵值本身「不是數字」由 worklogDay.test.ts 的 selectToAssignment 測試驗；
  // 這裡只驗選單裡沒有多出一個空值／預設選項可以被誤點。
  it('選單只有「未指派」＋每個候選各一項，沒有多出空值選項', async () => {
    mount()
    await userEvent.click(trigger())
    const options = within(await screen.findByRole('listbox')).getAllByRole('option')
    expect(options.length).toBe(CANDIDATES.length + 1)
  })
})

describe('WorklogEntryCard：確認就存（D-16）', () => {
  it('時數：打完 blur 才存，只帶 hours', async () => {
    const { onSave } = mount()
    await userEvent.clear(hoursInput())
    await userEvent.type(hoursInput(), '3.50')
    expect(onSave).not.toHaveBeenCalled()
    await userEvent.tab()
    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith(11, { hours: '3.50' })
  })

  it('時數：Enter 存一次，接著的 blur 不再存第二次', async () => {
    const { onSave } = mount()
    await userEvent.clear(hoursInput())
    await userEvent.type(hoursInput(), '1.50{Enter}')
    await userEvent.tab()
    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith(11, { hours: '1.50' })
  })

  it('時數清空 → 存 null（沒有時數），不是空字串或 0', async () => {
    const { onSave } = mount()
    await userEvent.clear(hoursInput())
    await userEvent.tab()
    expect(onSave).toHaveBeenCalledWith(11, { hours: null })
  })

  it('沒改就 blur 不存', async () => {
    const { onSave } = mount()
    await userEvent.click(hoursInput())
    await userEvent.click(noteInput())
    await userEvent.tab()
    expect(onSave).not.toHaveBeenCalled()
  })

  it('備註：blur 存、只帶 note；Shift+Enter 換行不存', async () => {
    const { onSave } = mount(entry({ note: '' }))
    await userEvent.type(noteInput(), '第一行{Shift>}{Enter}{/Shift}第二行')
    expect(onSave).not.toHaveBeenCalled()
    await userEvent.tab()
    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith(11, { note: '第一行\n第二行' })
  })

  it('打了字還沒確認 → 回報髒值；伺服器值跟上之後回報乾淨', async () => {
    const { onDirtyChange, view } = mount()
    await userEvent.type(hoursInput(), '5')
    expect(onDirtyChange).toHaveBeenLastCalledWith(11, true)
    const typed = hoursInput().value
    view.rerender(
      <WorklogEntryCard entry={entry({ hours: typed })} onSave={vi.fn()} onDirtyChange={onDirtyChange} />,
    )
    expect(onDirtyChange).toHaveBeenLastCalledWith(11, false)
  })

  it('存檔失敗：顯示錯誤、保留 Viewer 的輸入', async () => {
    const onDirtyChange = vi.fn()
    const { view } = mount()
    await userEvent.clear(hoursInput())
    await userEvent.type(hoursInput(), '9.99')
    await userEvent.tab()
    view.rerender(
      <WorklogEntryCard entry={entry()} error="存不進去" onSave={vi.fn()} onDirtyChange={onDirtyChange} />,
    )
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(hoursInput().value).toBe('9.99')
  })

  it('正對照：沒有錯誤時不顯示 alert', () => {
    mount()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('WorklogEntryCard：已送出的筆唯讀（D-21）', () => {
  it('有 worklog id → 歸屬、時數、備註全部停用', () => {
    mount(entry({ state: 'submitted', zplanner_worklog_id: 9931, issue_id: 501, issue_key: 'PEI-133' }))
    expect(hoursInput()).toBeDisabled()
    expect(noteInput()).toBeDisabled()
    expect(trigger()).toBeDisabled()
  })

  it('正對照：failed（沒有 worklog id）改得動', () => {
    mount(entry({ state: 'failed', last_error_message: '沒有權限' }))
    expect(hoursInput()).toBeEnabled()
    expect(noteInput()).toBeEnabled()
    expect(trigger()).toBeEnabled()
  })
})

describe('WorklogEntryCard：混合狀態可辨識（AC-10）', () => {
  const STATES: WorklogEntryState[] = ['pending', 'submitted', 'skipped', 'failed', 'unknown']

  it('五種狀態各自畫出不同的標籤', () => {
    const labels = STATES.map((state) => {
      const e = entry({ state, zplanner_worklog_id: state === 'submitted' ? 9931 : null })
      const { view } = mount(e)
      const label = entryStateText(e).label
      expect(within(view.container).getByText(label)).toBeInTheDocument()
      view.unmount()
      return label
    })
    expect(new Set(labels).size).toBe(STATES.length)
  })

  it('failed 把後端給的失敗原因畫出來（資料，不是文案）', () => {
    mount(entry({ state: 'failed', last_error_message: 'ZPLANNER 回 403 權限不足' }))
    expect(screen.getByText('ZPLANNER 回 403 權限不足')).toBeInTheDocument()
  })

  it('D-9 對不到分群（reason 為 null）時仍有說明，不留白', () => {
    const { view } = mount(entry({ cluster_key: null, label: null, reason: null }))
    const article = within(view.container).getByRole('article')
    expect(article.querySelector('p')?.textContent?.trim()).toBeTruthy()
  })
})
