import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { WorklogEntryCard } from '@/components/worklog/WorklogEntryCard'
import { entryStateText, type WorklogEntryPatch } from '@/store/worklogDay'
import type {
  WorklogCandidate,
  WorklogDayEntry,
  WorklogEntryState,
  WorklogIssueSearchResponse,
} from '@/lib/types'

/**
 * 一張草稿卡（AC-3、D-16、AC-10）。
 *
 * 守的是三件「畫面看起來沒事」的壞法：
 *
 * 1. **歸屬預選**：候選排第一的、或「上次記到」的那個被當成已選，Viewer 一路按送出
 *    就送了系統猜的單（ADR-0008 的唯一硬規則）。
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

const LAST_USED = candidate({
  issue_id: 777,
  issue_key: 'OPS-9',
  title: '',
  project_id: 77,
  project_key: 'OPS',
  source: 'last_used',
  confidence: 'medium',
  reason: '「匯出逾時」上次記到 OPS-9',
  last_used_at: '2026-09-12T00:00:00+00:00',
})

/** 產草稿時的 `generation_meta.zplanner` 段（只放卡片讀得到的欄位）。 */
function meta(zplanner: Record<string, unknown>) {
  return { clusters: [], zplanner: { reason: '', errors: [], ...zplanner } }
}
const META_OK = meta({ available: true })

type SearchFn = (q: string, signal?: AbortSignal) => Promise<WorklogIssueSearchResponse>

function mount(
  e: WorklogDayEntry = entry(),
  extra: { error?: string | null; saving?: boolean; generationMeta?: unknown; searchIssues?: SearchFn } = {},
) {
  const onSave = vi.fn(async (_id: number, _patch: WorklogEntryPatch) => true)
  const onDirtyChange = vi.fn()
  const searchIssues =
    extra.searchIssues ?? vi.fn<SearchFn>(async (q) => ({ q, count: 0, groups: [] }))
  const view = render(
    <WorklogEntryCard
      entry={e}
      onSave={onSave}
      onDirtyChange={onDirtyChange}
      generationMeta={META_OK}
      {...extra}
      searchIssues={searchIssues}
    />,
  )
  return { onSave, onDirtyChange, searchIssues, view }
}

const trigger = () => screen.getByRole('combobox', { name: '記到哪張單' })
const hoursInput = () => screen.getByLabelText('時數') as HTMLInputElement
const noteInput = () => screen.getByLabelText('備註') as HTMLTextAreaElement
const searchBox = () => screen.getByRole('combobox', { name: '搜尋可記工時的單' })

async function openPicker() {
  await userEvent.click(trigger())
  return screen.findByRole('listbox')
}

async function pickOption(optionText: string | RegExp) {
  const listbox = await openPicker()
  await userEvent.click(within(listbox).getByText(optionText))
}

describe('WorklogEntryCard：歸屬一律從未指派開始（AC-3、ADR-0008）', () => {
  it('🔴 產生後未指派：觸發鈕顯示「未指派」，不是排第一的候選', () => {
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
    mount(entry({ issue_id: 778, issue_key: 'OPS-10', candidates: CANDIDATES }))
    expect(trigger()).toHaveTextContent('OPS-10')
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

  it('再點一次目前已選的那一項 → 不重存（沒有變更就沒有 PUT）', async () => {
    const { onSave } = mount(entry({ issue_id: 502, issue_key: 'PEI-140', project_id: 43, project_key: 'OPS' }))
    await pickOption(/PEI-140/)
    expect(onSave).not.toHaveBeenCalled()
  })

  it('選單只有「未指派」＋每個候選各一項，沒有多出空值選項', async () => {
    mount()
    const options = within(await openPicker()).getAllByRole('option')
    expect(options.length).toBe(CANDIDATES.length + 1)
  })
})

describe('WorklogEntryCard：「上次記到」要點選才套用（ADR-0008 的 2026-10-01 補充）', () => {
  const withMemory = () => entry({ candidates: [LAST_USED, ...CANDIDATES] })

  it('🔴 有記憶時仍顯示「未指派」，而且沒有任何存檔', () => {
    const { onSave } = mount(withMemory())
    expect(trigger()).toHaveTextContent('未指派')
    expect(trigger()).not.toHaveTextContent('OPS-9')
    expect(onSave).not.toHaveBeenCalled()
  })

  it('「上次記到」排在選單第一項', async () => {
    mount(withMemory())
    const options = within(await openPicker()).getAllByRole('option')
    expect(options[0]).toHaveTextContent('OPS-9')
    expect(options).toHaveLength(CANDIDATES.length + 2)
  })

  it('與 commit 直接寫的單號合併過 → 標強訊號的信心度，不被記憶降級', async () => {
    const merged = candidate({
      ...LAST_USED,
      issue_id: 501,
      issue_key: 'PEI-133',
      title: '匯出逾時',
      confidence: 'strong',
      matched_source: 'issue_key',
      reason: '「cs-backend」上次記到 PEI-133；你在 commit 訊息裡直接寫了 PEI-133',
    })
    const { view } = mount(entry({ candidates: [merged] }))
    const first = within(await openPicker()).getAllByRole('option')[0]
    expect(first).toHaveTextContent('PEI-133')
    expect(first).toHaveTextContent('強') // 信心度等級是資料
    // 合併過就是命中，不顯示「沒命中」
    expect(view.container.querySelector('[data-lookup="no-match"]')).toBeNull()
  })

  it('點了才套用：四欄一起存', async () => {
    const { onSave } = mount(withMemory())
    const listbox = await openPicker()
    await userEvent.click(within(listbox).getByRole('option', { name: /^OPS-9/ }))
    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith(11, { issue_id: 777, issue_key: 'OPS-9', project_id: 77, project_key: 'OPS' })
  })
})

describe('WorklogEntryCard：搜尋全部可記工時的單', () => {
  const RESULT: WorklogIssueSearchResponse = {
    q: '備份',
    count: 2,
    groups: [
      { project_id: 77, project_key: 'OPS', issues: [{ issue_id: 20, issue_key: 'OPS-7', title: '設定備份', project_id: 77, project_key: 'OPS' }] },
      { project_id: 42, project_key: 'PEI', issues: [{ issue_id: 30, issue_key: 'PEI-300', title: '備份排程', project_id: 42, project_key: 'PEI' }] },
    ],
  }

  it('輸入關鍵字 → 去掉前後空白後搜尋，結果依專案分組，點了就存四欄', async () => {
    const searchIssues = vi.fn<SearchFn>(async () => RESULT)
    const { onSave } = mount(entry(), { searchIssues })
    await openPicker()
    await userEvent.type(searchBox(), '  備份 ')
    await waitFor(() => expect(searchIssues).toHaveBeenCalled())
    expect(searchIssues.mock.calls.at(-1)?.[0]).toBe('備份')

    const listbox = await screen.findByRole('listbox')
    await waitFor(() => expect(within(listbox).getByText(/PEI-300/)).toBeInTheDocument())
    // 分組的標題是資料（專案代號），不是文案
    expect(within(listbox).getByText('OPS')).toBeInTheDocument()
    expect(within(listbox).getByText('PEI')).toBeInTheDocument()

    await userEvent.click(within(listbox).getByText(/PEI-300/))
    expect(onSave).toHaveBeenCalledWith(11, { issue_id: 30, issue_key: 'PEI-300', project_id: 42, project_key: 'PEI' })
  })

  it('搜尋失敗 → 狀態列說失敗並給重試，按下用同一個關鍵字再查一次', async () => {
    let attempt = 0
    const searchIssues = vi.fn<SearchFn>(async () => {
      attempt += 1
      if (attempt === 1) throw new Error('連線逾時')
      return RESULT
    })
    mount(entry(), { searchIssues })
    await openPicker()
    await userEvent.type(searchBox(), '備份')
    const retry = await screen.findByRole('button', { name: '重試' })
    await userEvent.click(retry)
    await waitFor(() => expect(searchIssues).toHaveBeenCalledTimes(2))
    expect(searchIssues.mock.calls[1][0]).toBe('備份')
    await waitFor(() => expect(within(screen.getByRole('listbox')).getByText(/PEI-300/)).toBeInTheDocument())
  })

  it('🔴 只打空白不會送出搜尋（ZPlanner 對空字串回 0 筆而不是全部）', async () => {
    const searchIssues = vi.fn<SearchFn>(async () => RESULT)
    mount(entry(), { searchIssues })
    await openPicker()
    await userEvent.type(searchBox(), '   ')
    await new Promise((r) => setTimeout(r, 450)) // 超過 debounce
    expect(searchIssues).not.toHaveBeenCalled()
    // 正對照：清單仍是系統候選，沒有被換成空的搜尋結果
    expect(within(screen.getByRole('listbox')).getAllByRole('option')).toHaveLength(CANDIDATES.length + 1)
  })
})

describe('WorklogEntryCard：三種「沒有系統候選」分開顯示', () => {
  const noCandidates = () => entry({ candidates: [] })
  const lookupOf = (container: HTMLElement) =>
    Array.from(container.querySelectorAll('[data-lookup]')).map((el) => el.getAttribute('data-lookup'))

  it('ZPlanner 不可用 → 說明原因，而且搜尋框停用', async () => {
    const { view } = mount(noCandidates(), {
      generationMeta: meta({ available: false, reason: '未設定 ZPLANNER_BASE_URL' }),
    })
    expect(lookupOf(view.container)).toEqual(['unavailable'])
    // 原因是後端給的資料，要原樣出現
    expect(screen.getByRole('note')).toHaveTextContent('未設定 ZPLANNER_BASE_URL')
    await openPicker()
    expect(searchBox()).toBeDisabled()
  })

  it('正對照：ZPlanner 可用時搜尋框可以打字', async () => {
    mount(noCandidates())
    await openPicker()
    expect(searchBox()).toBeEnabled()
  })

  it('這一段的查詢失敗 → 有重試，按下用那次的關鍵字重查', async () => {
    const searchIssues = vi.fn<SearchFn>(async (q) => ({ q, count: 0, groups: [] }))
    const { view } = mount(noCandidates(), {
      searchIssues,
      generationMeta: meta({
        available: true,
        errors: [{ cluster: 'c-a', term: '匯出逾時', reason: 'ZPlanner 連線逾時' }],
      }),
    })
    expect(lookupOf(view.container)).toEqual(['query-failed'])
    await userEvent.click(screen.getByRole('button', { name: '重試查詢' }))
    await waitFor(() => expect(searchIssues).toHaveBeenCalledWith('匯出逾時', expect.anything()))
    await new Promise((r) => setTimeout(r, 450)) // 超過 debounce：重試不該再經 debounce 多查一次
    expect(searchIssues).toHaveBeenCalledTimes(1)
  })

  it('別段的查詢失敗不算這一段的', () => {
    const { view } = mount(noCandidates(), {
      generationMeta: meta({ available: true, errors: [{ cluster: 'c-other', term: 'x', reason: '逾時' }] }),
    })
    expect(lookupOf(view.container)).toEqual(['no-match'])
  })

  it('有查但沒命中 → 請 Viewer 自己搜', () => {
    const { view } = mount(noCandidates())
    expect(lookupOf(view.container)).toEqual(['no-match'])
  })

  it('只有「上次記到」也算沒命中（記憶不是查詢結果）', () => {
    const { view } = mount(entry({ candidates: [LAST_USED] }))
    expect(lookupOf(view.container)).toEqual(['no-match'])
  })

  it('正對照：有系統候選時不顯示任何一種說明', () => {
    const { view } = mount()
    expect(lookupOf(view.container)).toEqual([])
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
