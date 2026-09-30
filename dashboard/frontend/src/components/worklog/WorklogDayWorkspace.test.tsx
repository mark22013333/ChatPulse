import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WorklogDayWorkspace } from '@/components/worklog/WorklogDayWorkspace'
import { ApiError, api } from '@/lib/api'
import * as sse from '@/lib/sse'
import type { WorklogDay, WorklogDayEntry, WorklogSubmitSummary } from '@/lib/types'
import { useWorklogStore } from '@/store/worklog'
import { useWorklogDayStore } from '@/store/worklogDay'
import { RouterProvider, useRoute } from '@/router/useRouter'
import { useRouteSync } from '@/router/useRouteSync'

/**
 * 單日工作區（D-18）。用**真的** store，外部只攔 `api.*` 與 `streamSse`：
 * 這一層要驗的正是「元件有沒有把 store 的 action 接上」，把 store 換成假的就驗不到了。
 *
 * 守的 AC：
 * - AC-1：尚無草稿時有「產生草稿」入口；讀取失敗時**沒有**（那會引導 Viewer 去重產）。
 * - AC-2：換日期中止串流（元件不卸載，只換 `date` prop）。
 * - AC-4：重產被已指派守門擋下 → 二次確認，確認才帶 force。
 * - AC-10：送出前二次確認。
 * - AC-11：409 正在送出中有提示且不自動重試；not_attempted_count > 0 有提示。
 *
 * 🔴 不斷言文案措辭：用 role／可及名稱找元素，斷言呼叫次數、參數與「有沒有出現」。
 */

const DATE = '2026-09-13'
const OTHER = '2026-09-14'

function entry(over: Partial<WorklogDayEntry> = {}): WorklogDayEntry {
  return {
    id: 11,
    draft_id: 1,
    seq: 1,
    cluster_key: 'c-a',
    label: '匯出逾時',
    reason: '同一個 issue key',
    issue_id: 501,
    issue_key: 'PEI-133',
    project_id: 42,
    project_key: 'PEI',
    hours: '2.00',
    note: '修正匯出逾時',
    allocated_hours: '2.00',
    weight: 1,
    rounded_up: false,
    evidence_refs: ['commit:aaa'],
    candidates: [],
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

function day(over: Partial<WorklogDay> = {}): WorklogDay {
  return {
    id: 1,
    work_date: DATE,
    status: 'draft',
    total_hours: '8.00',
    tz_offset: '+08:00',
    generation_meta: {},
    generated_at: null,
    created_at: '2026-09-13T00:00:00+00:00',
    updated_at: '2026-09-13T00:00:00+00:00',
    entries: [entry()],
    ...over,
  }
}

function summary(over: Partial<WorklogSubmitSummary> = {}): WorklogSubmitSummary {
  return {
    submitted_count: 1,
    skipped_count: 0,
    failed_count: 0,
    unknown_count: 0,
    skipped_duplicates: [],
    orphans: [],
    not_attempted_count: 0,
    ...over,
  }
}

const notFound = () => new ApiError(404, 'WORKLOG_DRAFT_NOT_FOUND', '還沒有工時草稿')

/** 掛著不結束的串流：記下每次呼叫的 body 與 signal。 */
function hangingStream() {
  const calls: { body: Record<string, unknown>; signal: AbortSignal }[] = []
  vi.spyOn(sse, 'streamSse').mockImplementation(async (_url, body, _handlers, signal) => {
    calls.push({ body: body as Record<string, unknown>, signal })
    await new Promise<void>((resolve) => signal.addEventListener('abort', () => resolve()))
  })
  return calls
}

/** 直接灌一份 ready 狀態；`date` 與 prop 相同，所以 `open()` 不會再打 API。 */
function seedReady(over: Record<string, unknown> = {}) {
  useWorklogDayStore.setState({ date: DATE, phase: { kind: 'ready' }, day: day(), ...over })
}

beforeEach(() => {
  vi.restoreAllMocks()
  useWorklogDayStore.getState().reset()
  useWorklogStore.setState({ loadWeek: vi.fn(async () => {}) } as never)
})

describe('WorklogDayWorkspace：入口（AC-1）', () => {
  it('尚無草稿 → 出現「產生草稿」，按下去開始串流', async () => {
    vi.spyOn(api, 'worklogDay').mockRejectedValue(notFound())
    const calls = hangingStream()
    render(<WorklogDayWorkspace date={DATE} />)
    const button = await screen.findByRole('button', { name: '產生草稿' })
    await userEvent.click(button)
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0].body.force).toBe(false)
    expect(await screen.findByRole('status', { name: '正在產生草稿' })).toBeInTheDocument()
  })

  it('有填總時數 → 帶進串流 body', async () => {
    vi.spyOn(api, 'worklogDay').mockRejectedValue(notFound())
    const calls = hangingStream()
    render(<WorklogDayWorkspace date={DATE} />)
    const region = await screen.findByRole('region', { name: '尚無草稿' })
    await userEvent.type(within(region).getByLabelText('當天總時數'), '7.5')
    await userEvent.click(within(region).getByRole('button', { name: '產生草稿' }))
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0].body.total_hours).toBe(7.5)
  })

  it('🔴 讀取失敗（不是 404）→ 沒有「產生草稿」，只有重讀', async () => {
    const get = vi.spyOn(api, 'worklogDay').mockRejectedValue(new ApiError(500, 'INTERNAL_ERROR', '炸了'))
    render(<WorklogDayWorkspace date={DATE} />)
    const retry = await screen.findByRole('button', { name: '重讀' })
    expect(screen.queryByRole('button', { name: '產生草稿' })).toBeNull()
    await userEvent.click(retry)
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('沒選日期 → 不打 API、沒有入口', () => {
    const get = vi.spyOn(api, 'worklogDay')
    render(<WorklogDayWorkspace date={null} />)
    expect(get).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '產生草稿' })).toBeNull()
  })

  it('有草稿 → 畫草稿卡，不畫入口', async () => {
    vi.spyOn(api, 'worklogDay').mockResolvedValue(day({ entries: [entry(), entry({ id: 12, seq: 2 })] }))
    render(<WorklogDayWorkspace date={DATE} />)
    const list = await screen.findByRole('list', { name: '草稿卡' })
    expect(within(list).getAllByRole('article')).toHaveLength(2)
    expect(screen.queryByRole('button', { name: '產生草稿' })).toBeNull()
  })
})

describe('WorklogDayWorkspace：換日期中止串流（AC-2）', () => {
  it('串流中換 date prop → 舊串流被中止、改讀新的一天', async () => {
    const get = vi.spyOn(api, 'worklogDay').mockRejectedValue(notFound())
    const calls = hangingStream()
    const { rerender } = render(<WorklogDayWorkspace date={DATE} />)
    await userEvent.click(await screen.findByRole('button', { name: '產生草稿' }))
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0].signal.aborted).toBe(false)

    rerender(<WorklogDayWorkspace date={OTHER} />)
    await waitFor(() => expect(calls[0].signal.aborted).toBe(true))
    expect(get).toHaveBeenLastCalledWith(OTHER)
    expect(await screen.findByRole('button', { name: '產生草稿' })).toBeInTheDocument()
  })

  it('卸載時中止串流', async () => {
    vi.spyOn(api, 'worklogDay').mockRejectedValue(notFound())
    const calls = hangingStream()
    const { unmount } = render(<WorklogDayWorkspace date={DATE} />)
    await userEvent.click(await screen.findByRole('button', { name: '產生草稿' }))
    await waitFor(() => expect(calls).toHaveLength(1))
    unmount()
    expect(calls[0].signal.aborted).toBe(true)
  })

  it('正對照：同一個 date 重新 render 不中止', async () => {
    vi.spyOn(api, 'worklogDay').mockRejectedValue(notFound())
    const calls = hangingStream()
    const { rerender } = render(<WorklogDayWorkspace date={DATE} />)
    await userEvent.click(await screen.findByRole('button', { name: '產生草稿' }))
    await waitFor(() => expect(calls).toHaveLength(1))
    rerender(<WorklogDayWorkspace date={DATE} />)
    expect(calls[0].signal.aborted).toBe(false)
    useWorklogDayStore.getState().reset()
  })
})

describe('WorklogDayWorkspace：重產的二次確認（AC-4）', () => {
  it('pendingForce 有值 → 對話框出現；確認後以 force:true 與同一個總時數重送', async () => {
    seedReady({ pendingForce: { assignedCount: 2, assignedIssueKeys: ['PEI-133', 'PEI-140'], totalHours: 8 } })
    const calls = hangingStream()
    render(<WorklogDayWorkspace date={DATE} />)
    const dialog = await screen.findByRole('dialog')
    // 筆數是資料，不是文案：數字一定要出現在說明裡
    expect(dialog).toHaveTextContent('2')
    await userEvent.click(within(dialog).getByRole('button', { name: '清空並重新產生' }))
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0].body.force).toBe(true)
    expect(calls[0].body.total_hours).toBe(8)
    useWorklogDayStore.getState().reset()
  })

  it('正對照：沒有 pendingForce 就沒有對話框', () => {
    seedReady()
    render(<WorklogDayWorkspace date={DATE} />)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('WorklogDayWorkspace：送出（AC-10、AC-11）', () => {
  it('送出前有二次確認：按「送出到 ZPlanner」還不送，按「確認送出」才送', async () => {
    seedReady()
    const submit = vi
      .spyOn(api, 'submitWorklog')
      .mockResolvedValue({ work_date: DATE, status: 'submitted', entries: [], summary: summary() })
    vi.spyOn(api, 'worklogDay').mockResolvedValue(day())
    render(<WorklogDayWorkspace date={DATE} />)
    await userEvent.click(screen.getByRole('button', { name: '送出到 ZPlanner' }))
    const dialog = await screen.findByRole('dialog')
    expect(submit).not.toHaveBeenCalled()
    await userEvent.click(within(dialog).getByRole('button', { name: '確認送出' }))
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1))
  })

  it('🔴 409 WORKLOG_SUBMIT_IN_PROGRESS → 顯示提示與重讀鈕，且不自動重試', async () => {
    seedReady()
    const submit = vi
      .spyOn(api, 'submitWorklog')
      .mockRejectedValue(new ApiError(409, 'WORKLOG_SUBMIT_IN_PROGRESS', '正在送', null, { work_date: DATE }))
    render(<WorklogDayWorkspace date={DATE} />)
    await userEvent.click(screen.getByRole('button', { name: '送出到 ZPlanner' }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '確認送出' }))

    const section = screen.getByRole('region', { name: '送出' })
    const alert = await within(section).findByRole('alert')
    expect(within(alert).getByRole('button', { name: '重讀' })).toBeInTheDocument()
    await new Promise((r) => setTimeout(r, 100))
    expect(submit).toHaveBeenCalledTimes(1)
  })

  it('正對照：其他送出錯誤沒有「正在送出中」的重讀鈕', async () => {
    seedReady({ submitError: { code: 'ZPLANNER_UNAVAILABLE', message: 'ZPlanner 掛了', detail: null } })
    render(<WorklogDayWorkspace date={DATE} />)
    const alert = within(screen.getByRole('region', { name: '送出' })).getByRole('alert')
    expect(within(alert).queryByRole('button', { name: '重讀' })).toBeNull()
  })

  it('not_attempted_count > 0 → 送出結果多一行，帶出筆數', () => {
    seedReady({ submitSummary: summary({ not_attempted_count: 3 }) })
    const { unmount } = render(<WorklogDayWorkspace date={DATE} />)
    const partial = within(screen.getByRole('status', { name: '送出結果' })).getAllByText(/./)
    expect(partial.some((p) => p.textContent?.includes('3'))).toBe(true)
    const partialLines = partial.length
    unmount()

    // 正對照：全部跑完時少那一行
    seedReady({ submitSummary: summary({ not_attempted_count: 0 }) })
    render(<WorklogDayWorkspace date={DATE} />)
    const full = within(screen.getByRole('status', { name: '送出結果' })).getAllByText(/./)
    expect(full.length).toBe(partialLines - 1)
  })

  it('卡片上打了字還沒確認 → 送出鈕停用（D-16）；確認並存好後恢復', async () => {
    // ⚠️ 不能直接灌 dirtyIds：卡片掛載時會以自己的輸入框回報 dirty=false，
    // 髒值的唯一來源是卡片本身。所以這裡真的去打字。
    seedReady()
    const put = vi.spyOn(api, 'updateWorklogEntries').mockResolvedValue(day({ entries: [entry({ hours: '3.00' })] }))
    render(<WorklogDayWorkspace date={DATE} />)
    const submitButton = screen.getByRole('button', { name: '送出到 ZPlanner' })
    expect(submitButton).toBeEnabled()

    const hours = screen.getByLabelText('時數')
    await userEvent.clear(hours)
    await userEvent.type(hours, '3.00')
    expect(submitButton).toBeDisabled()

    await userEvent.tab()
    await waitFor(() => expect(put).toHaveBeenCalledWith(DATE, [{ id: 11, hours: '3.00' }]))
    await waitFor(() => expect(submitButton).toBeEnabled())
  })
})


// ── AC-2 回歸：切 section 不中止串流（plan-verify 抓到的 FAIL）──────────
//
// 🔴 壞法：串流途中切到別的 section，`route.worklogDate` 變成 null，Workspace
// 把 null 當成「換日期」而 abort 了串流——但 Pane 刻意常駐掛載，切頁籤本來
// 就不該中止（AC-2）。Viewer 看到的是：切去回一則 Mention、再回來，草稿不見了。
//
// abort 一律讀傳進 streamSse 的 **AbortSignal 本身**，不看畫面文字：
// 畫面可能因為別的原因還顯示著「產生中」。

const D14 = '2026-09-14'
const D15 = '2026-09-15'

/** 按下產生、讓串流掛著；回傳那次 streamSse 收到的 signal。 */
async function startStreaming(calls: ReturnType<typeof hangingStream>) {
  await userEvent.click(await screen.findByRole('button', { name: '產生草稿' }))
  await waitFor(() => expect(calls).toHaveLength(1))
  expect(calls[0].signal.aborted).toBe(false)
  return calls[0].signal
}

describe('WorklogDayWorkspace：date prop 變成 null 不中止串流（AC-2 回歸）', () => {
  it('🔴 串流中 date → null → 不 abort，store 仍停在原本那天', async () => {
    vi.spyOn(api, 'worklogDay').mockRejectedValue(notFound())
    const calls = hangingStream()
    const { rerender } = render(<WorklogDayWorkspace date={D14} />)
    const signal = await startStreaming(calls)

    rerender(<WorklogDayWorkspace date={null} />)
    await flushEffects()
    expect(signal.aborted).toBe(false)
    expect(useWorklogDayStore.getState().date).toBe(D14)
    expect(useWorklogDayStore.getState().phase.kind).toBe('streaming')

    // 回到沒有日期的 #/worklog：date 仍是 null，仍然不中止
    rerender(<WorklogDayWorkspace date={null} />)
    await flushEffects()
    expect(signal.aborted).toBe(false)
    // 標題讀的是 date prop，prop 為 null 時不顯示日期；實際 app 裡這只發生在
    // Pane 隱藏時，或 useRouteSync 補回網址前的一瞬間。所以「畫面維持 09-14」
    // 由下面網址驅動的那組測試驗（那條才是 Viewer 真的會走的路）。
    expect(useWorklogDayStore.getState().date).toBe(D14)
    useWorklogDayStore.getState().reset()
  })

  it('null 之後回到同一天 → 不 abort、不重開串流', async () => {
    vi.spyOn(api, 'worklogDay').mockRejectedValue(notFound())
    const calls = hangingStream()
    const { rerender } = render(<WorklogDayWorkspace date={D14} />)
    const signal = await startStreaming(calls)
    rerender(<WorklogDayWorkspace date={null} />)
    rerender(<WorklogDayWorkspace date={D14} />)
    await flushEffects()
    expect(signal.aborted).toBe(false)
    expect(calls).toHaveLength(1)
    useWorklogDayStore.getState().reset()
  })

  it('正對照：null 之後換到另一個有效日期 → abort，改讀那一天', async () => {
    const get = vi.spyOn(api, 'worklogDay').mockRejectedValue(notFound())
    const calls = hangingStream()
    const { rerender } = render(<WorklogDayWorkspace date={D14} />)
    const signal = await startStreaming(calls)
    rerender(<WorklogDayWorkspace date={null} />)
    rerender(<WorklogDayWorkspace date={D15} />)
    await waitFor(() => expect(signal.aborted).toBe(true))
    expect(get).toHaveBeenLastCalledWith(D15)
    expect(useWorklogDayStore.getState().date).toBe(D15)
  })

  it('正對照：卸載時 abort（null 不中止不代表永遠不中止）', async () => {
    vi.spyOn(api, 'worklogDay').mockRejectedValue(notFound())
    const calls = hangingStream()
    const { rerender, unmount } = render(<WorklogDayWorkspace date={D14} />)
    const signal = await startStreaming(calls)
    rerender(<WorklogDayWorkspace date={null} />)
    await flushEffects()
    expect(signal.aborted).toBe(false)
    unmount()
    expect(signal.aborted).toBe(true)
  })
})

/**
 * 照 AppShell 的接法：`useRouteSync()` ＋ `<WorklogDayWorkspace date={route.worklogDate} />`
 * （AppShell.tsx:57、:257）。用真的 `window.location.hash` 切 section，
 * 這樣「網址 → route → 週欄選取日 → 中欄」整條鏈都在測試裡。
 * ⚠️ 若 AppShell 的接法改了，這個 harness 要跟著改，否則測的是另一條路。
 */
function Shell() {
  useRouteSync()
  const route = useRoute()
  return <WorklogDayWorkspace date={route.worklogDate} />
}

async function go(hash: string) {
  await act(async () => {
    window.location.hash = hash
    await new Promise((r) => setTimeout(r, 0))
  })
}

async function flushEffects() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0))
  })
}

describe('AC-2 回歸：真的切 section（網址驅動）', () => {
  beforeEach(() => {
    useWorklogStore.setState({ selectedDate: null } as never)
    window.history.replaceState(null, '', `#/worklog/${D14}`)
  })

  // S4 改寫：工時草稿改成表格型之後，`#/worklog` 是「整頁表格」這個真的位置，
  // useRouteSync 不再把網址補回上次那一天（補了的話返回鍵永遠回不到表格）。
  // 原本斷言「網址被補成 #/worklog/09-14、標題是 09-14」，現在改成：網址停在
  // #/worklog，但串流仍不中止、單日 store 仍停在 09-14；再點回 09-14 那一列時
  // 不重打、不中止——AC-2（切頁籤不中止串流）這條核心不變。
  it('🔴 #/worklog/09-14 串流中 → #/mentions → #/worklog（表格）→ 再點回 09-14：全程不 abort、不重打', async () => {
    vi.spyOn(api, 'worklogDay').mockRejectedValue(notFound())
    const calls = hangingStream()
    render(
      <RouterProvider>
        <Shell />
      </RouterProvider>,
    )
    const signal = await startStreaming(calls)

    await go('#/mentions')
    expect(signal.aborted).toBe(false)

    await go('#/worklog')
    await flushEffects()
    // 表格型：網址不被補回日期
    expect(window.location.hash).toBe('#/worklog')
    expect(signal.aborted).toBe(false)
    // 單日工作區收起來但沒丟東西：store 仍是 09-14、選取日也還在
    expect(useWorklogDayStore.getState().date).toBe(D14)
    expect(useWorklogStore.getState().selectedDate).toBe(D14)

    // 從表格點回同一天
    await go(`#/worklog/${D14}`)
    await flushEffects()
    expect(signal.aborted).toBe(false)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(D14)
    expect(calls).toHaveLength(1)
    useWorklogDayStore.getState().reset()
  })

  it('週欄從沒選過日期：#/worklog 維持原樣，不補日期、不打 API（不猜「今天」）', async () => {
    window.history.replaceState(null, '', '#/worklog')
    const get = vi.spyOn(api, 'worklogDay')
    render(
      <RouterProvider>
        <Shell />
      </RouterProvider>,
    )
    await flushEffects()
    expect(window.location.hash).toBe('#/worklog')
    expect(useWorklogStore.getState().selectedDate).toBeNull()
    expect(get).not.toHaveBeenCalled()
    expect(useWorklogDayStore.getState().phase.kind).toBe('idle')
  })

  it('正對照：串流中網址換到另一天 → abort，三處都換成那一天', async () => {
    vi.spyOn(api, 'worklogDay').mockRejectedValue(notFound())
    const calls = hangingStream()
    render(
      <RouterProvider>
        <Shell />
      </RouterProvider>,
    )
    const signal = await startStreaming(calls)

    await go(`#/worklog/${D15}`)
    await waitFor(() => expect(signal.aborted).toBe(true))
    expect(window.location.hash).toBe(`#/worklog/${D15}`)
    expect(useWorklogDayStore.getState().date).toBe(D15)
    expect(useWorklogStore.getState().selectedDate).toBe(D15)
  })
})
