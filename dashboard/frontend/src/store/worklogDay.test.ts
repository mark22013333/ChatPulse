import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '@/lib/api'
import * as sse from '@/lib/sse'
import type { WorklogCandidate, WorklogDay, WorklogDayEntry, WorklogSubmitSummary } from '@/lib/types'
import { useWorklogStore } from './worklog'
import {
  UNASSIGNED,
  assignmentValue,
  readForceDetail,
  selectToAssignment,
  submitBlockReason,
  submitErrorText,
  submitSummaryLines,
  tzOffsetFor,
  useWorklogDayStore,
} from './worklogDay'

/**
 * 單日工時 store（D-15）。這裡守的都是「畫面看起來正常、資料卻錯了」的那種壞法：
 *
 * 1. **晚到的回應不准蓋掉現在這一天**（連點兩天、PUT 排隊中換日）。
 * 2. **讀取失敗不准畫成「沒有草稿」**——Viewer 會按下產生，帶 force 時清掉他選好的專案。
 * 3. **Select 清除給的 null 不准變成 0 或「清成未指派」**（AC-3、plan.md 風險段）。
 * 4. **409 正在送出中不自動重試**（AC-11）。自動重試正是那道鎖要防的動作。
 *
 * 串流以 `vi.spyOn(sse, 'streamSse')` 攔下（同 draft.test.ts），API 以 `vi.spyOn(api, …)`；
 * 不 mock 裸 fetch。🔴 不斷言文案措辭，斷言呼叫、狀態與數字。
 */

const DATE = '2026-09-13'
const OTHER = '2026-09-14'

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
    candidates: [candidate()],
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

/** 可手動決定何時 resolve 的 promise。 */
function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const flush = () => new Promise((r) => setTimeout(r, 0))

type Captured = { url: string; body: Record<string, unknown>; handlers: sse.SseHandlers<never, never, never>; signal: AbortSignal }

/**
 * 攔下 streamSse。`script` 決定這次串流要觸發哪些事件；不給就一直掛著，
 * 直到測試自己呼叫 `finish()`（模擬「串流進行中」）。
 */
function captureStream(script?: (h: Captured['handlers']) => void) {
  const calls: Captured[] = []
  const pending: Array<() => void> = []
  vi.spyOn(sse, 'streamSse').mockImplementation(async (url, body, handlers, signal) => {
    const call = { url, body: body as Record<string, unknown>, handlers: handlers as Captured['handlers'], signal }
    calls.push(call)
    if (script) {
      script(call.handlers)
      return
    }
    await new Promise<void>((r) => pending.push(r))
  })
  return { calls, finish: () => pending.splice(0).forEach((r) => r()) }
}

let loadWeek: ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.restoreAllMocks()
  useWorklogDayStore.getState().reset()
  loadWeek = vi.fn(async () => {})
  useWorklogStore.setState({ loadWeek } as never)
})

async function openReady(d: WorklogDay = day()) {
  vi.spyOn(api, 'worklogDay').mockResolvedValue(d)
  useWorklogDayStore.getState().open(DATE)
  await flush()
  expect(useWorklogDayStore.getState().phase.kind).toBe('ready')
}

// ── 讀取 ──────────────────────────────────────────────────────────────

describe('worklogDay store：換日與讀取', () => {
  it('open(date) 讀那一天，回應放進 day', async () => {
    const spy = vi.spyOn(api, 'worklogDay').mockResolvedValue(day())
    useWorklogDayStore.getState().open(DATE)
    expect(useWorklogDayStore.getState().phase.kind).toBe('loading')
    await flush()
    expect(spy).toHaveBeenCalledWith(DATE)
    const state = useWorklogDayStore.getState()
    expect(state.phase.kind).toBe('ready')
    expect(state.day?.entries[0].id).toBe(11)
  })

  it('同一天重複 open 不重打', async () => {
    const spy = vi.spyOn(api, 'worklogDay').mockResolvedValue(day())
    useWorklogDayStore.getState().open(DATE)
    useWorklogDayStore.getState().open(DATE)
    await flush()
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('404 WORKLOG_DRAFT_NOT_FOUND → absent（AC-1 的入口狀態）', async () => {
    vi.spyOn(api, 'worklogDay').mockRejectedValue(notFound())
    useWorklogDayStore.getState().open(DATE)
    await flush()
    expect(useWorklogDayStore.getState().phase.kind).toBe('absent')
  })

  it('🔴 其他讀取失敗是 load-failed，不准畫成 absent', async () => {
    vi.spyOn(api, 'worklogDay').mockRejectedValue(new ApiError(500, 'INTERNAL_ERROR', '炸了'))
    useWorklogDayStore.getState().open(DATE)
    await flush()
    expect(useWorklogDayStore.getState().phase.kind).toBe('load-failed')
  })

  it('已有草稿時重讀失敗：保留舊的 day，只標 reloadError', async () => {
    await openReady()
    vi.spyOn(api, 'worklogDay').mockRejectedValue(new ApiError(500, 'INTERNAL_ERROR', '炸了'))
    await useWorklogDayStore.getState().reload()
    const state = useWorklogDayStore.getState()
    expect(state.phase.kind).toBe('ready')
    expect(state.day).not.toBeNull()
    expect(state.reloadError).not.toBeNull()
  })

  it('store 的 open(null) 語意不變：中止串流並重置成 idle（「忽略 null」只在元件層）', async () => {
    vi.spyOn(api, 'worklogDay').mockRejectedValue(notFound())
    useWorklogDayStore.getState().open(DATE)
    await flush()
    const { calls, finish } = captureStream()
    const generating = useWorklogDayStore.getState().generate({ totalHours: null })
    await flush()
    expect(calls[0].signal.aborted).toBe(false)
    useWorklogDayStore.getState().open(null)
    expect(calls[0].signal.aborted).toBe(true)
    const state = useWorklogDayStore.getState()
    expect(state.date).toBeNull()
    expect(state.phase.kind).toBe('idle')
    finish()
    await generating
  })

  it('晚到的前一天回應不可以蓋掉現在這一天', async () => {
    const slow = deferred<WorklogDay>()
    vi.spyOn(api, 'worklogDay').mockImplementation((d: string) =>
      d === DATE ? slow.promise : Promise.resolve(day({ work_date: OTHER, entries: [entry({ id: 99 })] })),
    )
    useWorklogDayStore.getState().open(DATE)
    useWorklogDayStore.getState().open(OTHER)
    await flush()
    slow.resolve(day())
    await flush()
    const state = useWorklogDayStore.getState()
    expect(state.date).toBe(OTHER)
    expect(state.day?.work_date).toBe(OTHER)
    expect(state.day?.entries.map((e) => e.id)).toEqual([99])
  })
})

// ── 產生草稿（SSE）────────────────────────────────────────────────────

describe('worklogDay store：產生草稿', () => {
  beforeEach(async () => {
    vi.spyOn(api, 'worklogDay').mockRejectedValue(notFound())
    useWorklogDayStore.getState().open(DATE)
    await flush()
    vi.restoreAllMocks()
    useWorklogStore.setState({ loadWeek } as never)
  })

  it('body 帶那一天的 tz_offset 與 force=false；總時數 null 時省略', async () => {
    const { calls } = captureStream((h) => h.onDone?.({ type: 'done', draft_id: 1, allocation: null } as never))
    vi.spyOn(api, 'worklogDay').mockResolvedValue(day())
    await useWorklogDayStore.getState().generate({ totalHours: null })
    expect(calls).toHaveLength(1)
    expect(calls[0].url).toContain(`/worklogs/${DATE}/draft/stream`)
    expect(calls[0].body.tz_offset).toBe(tzOffsetFor(DATE))
    expect(calls[0].body.force).toBe(false)
    expect('total_hours' in calls[0].body).toBe(false)
  })

  it('有總時數時帶上', async () => {
    const { calls } = captureStream((h) => h.onDone?.({ type: 'done', draft_id: 1, allocation: null } as never))
    vi.spyOn(api, 'worklogDay').mockResolvedValue(day())
    await useWorklogDayStore.getState().generate({ totalHours: 7.5 })
    expect(calls[0].body.total_hours).toBe(7.5)
  })

  it('done 之後卡片來自 GET（D-10），並刷新週欄', async () => {
    captureStream((h) => h.onDone?.({ type: 'done', draft_id: 1, allocation: null } as never))
    const get = vi.spyOn(api, 'worklogDay').mockResolvedValue(day())
    await useWorklogDayStore.getState().generate({ totalHours: null })
    expect(get).toHaveBeenCalledWith(DATE)
    const state = useWorklogDayStore.getState()
    expect(state.phase.kind).toBe('ready')
    expect(state.day?.entries).toHaveLength(1)
    expect(loadWeek).toHaveBeenCalled()
  })

  it('done 後 GET 卻 404：不畫成 absent（草稿確定已落地）', async () => {
    captureStream((h) => h.onDone?.({ type: 'done', draft_id: 1, allocation: null } as never))
    vi.spyOn(api, 'worklogDay').mockRejectedValue(notFound())
    await useWorklogDayStore.getState().generate({ totalHours: null })
    expect(useWorklogDayStore.getState().phase.kind).not.toBe('absent')
  })

  it('AC-4：已指派守門的 400 → pendingForce 帶 detail 的筆數，不是一般錯誤', async () => {
    captureStream((h) =>
      h.onError?.({
        type: 'error',
        code: 'INVALID_PARAMETER',
        message: '有 2 筆已經選好專案了',
        detail: { assigned_count: 2, assigned_issue_keys: ['PEI-133', 'PEI-140'], force_allowed: true },
      }),
    )
    vi.spyOn(api, 'worklogDay').mockResolvedValue(day())
    await useWorklogDayStore.getState().generate({ totalHours: 8 })
    const state = useWorklogDayStore.getState()
    expect(state.pendingForce).toEqual({ assignedCount: 2, assignedIssueKeys: ['PEI-133', 'PEI-140'], totalHours: 8 })
    expect(state.generateError).toBeNull()
  })

  it('正對照：認不出的 400（沒有 force_allowed）走一般錯誤，不問要不要清空', async () => {
    captureStream((h) => h.onError?.({ type: 'error', code: 'INVALID_PARAMETER', message: 'tz_offset 格式錯' }))
    vi.spyOn(api, 'worklogDay').mockResolvedValue(day())
    await useWorklogDayStore.getState().generate({ totalHours: 8 })
    const state = useWorklogDayStore.getState()
    expect(state.pendingForce).toBeNull()
    expect(state.generateError?.code).toBe('INVALID_PARAMETER')
  })

  it('confirmForce 以同一個總時數帶 force:true 重送', async () => {
    const { calls } = captureStream((h) => {
      if (calls.length === 1) {
        h.onError?.({
          type: 'error',
          code: 'INVALID_PARAMETER',
          message: 'x',
          detail: { assigned_count: 1, assigned_issue_keys: ['PEI-133'], force_allowed: true },
        })
      } else {
        h.onDone?.({ type: 'done', draft_id: 1, allocation: null } as never)
      }
    })
    vi.spyOn(api, 'worklogDay').mockResolvedValue(day())
    await useWorklogDayStore.getState().generate({ totalHours: 6 })
    expect(useWorklogDayStore.getState().pendingForce).not.toBeNull()
    await useWorklogDayStore.getState().confirmForce()
    expect(calls).toHaveLength(2)
    expect(calls[1].body.force).toBe(true)
    expect(calls[1].body.total_hours).toBe(6)
    expect(useWorklogDayStore.getState().pendingForce).toBeNull()
  })

  it('cancelForce 不重送', async () => {
    const { calls } = captureStream((h) =>
      h.onError?.({
        type: 'error',
        code: 'INVALID_PARAMETER',
        message: 'x',
        detail: { assigned_count: 1, force_allowed: true },
      }),
    )
    vi.spyOn(api, 'worklogDay').mockResolvedValue(day())
    await useWorklogDayStore.getState().generate({ totalHours: null })
    useWorklogDayStore.getState().cancelForce()
    expect(useWorklogDayStore.getState().pendingForce).toBeNull()
    expect(calls).toHaveLength(1)
  })

  it('AC-2：換日期中止串流，晚到的事件不碰新一天的狀態', async () => {
    const { calls, finish } = captureStream()
    const generating = useWorklogDayStore.getState().generate({ totalHours: null })
    await flush()
    expect(useWorklogDayStore.getState().phase.kind).toBe('streaming')

    vi.spyOn(api, 'worklogDay').mockRejectedValue(notFound())
    useWorklogDayStore.getState().open(OTHER)
    expect(calls[0].signal.aborted).toBe(true)

    // 被中止的那條串流晚一步吐出事件
    calls[0].handlers.onMeta?.({ type: 'meta', work_date: DATE, clusters: [] } as never)
    calls[0].handlers.onDone?.({ type: 'done', draft_id: 1, allocation: null } as never)
    finish()
    await generating
    await flush()
    const state = useWorklogDayStore.getState()
    expect(state.date).toBe(OTHER)
    expect(state.stream.meta).toBeNull()
    expect(state.phase.kind).toBe('absent')
  })

  it('正對照：沒換日期時 meta 會進 stream', async () => {
    const { calls, finish } = captureStream()
    const generating = useWorklogDayStore.getState().generate({ totalHours: null })
    await flush()
    calls[0].handlers.onMeta?.({ type: 'meta', work_date: DATE, clusters: [] } as never)
    expect(useWorklogDayStore.getState().stream.meta).not.toBeNull()
    expect(calls[0].signal.aborted).toBe(false)
    vi.spyOn(api, 'worklogDay').mockRejectedValue(notFound())
    finish()
    await generating
  })

  it('abort() 中止串流（元件卸載）', async () => {
    const { calls, finish } = captureStream()
    const generating = useWorklogDayStore.getState().generate({ totalHours: null })
    await flush()
    vi.spyOn(api, 'worklogDay').mockRejectedValue(notFound())
    useWorklogDayStore.getState().abort()
    expect(calls[0].signal.aborted).toBe(true)
    finish()
    await generating
  })
})

// ── 逐筆存檔（PUT）────────────────────────────────────────────────────

describe('worklogDay store：updateEntry', () => {
  it('只帶這一筆，回應整份取代 day（D-10、D-16）', async () => {
    await openReady()
    const saved = day({ entries: [entry({ note: '改過', ...{ issue_id: 501, issue_key: 'PEI-133' } })] })
    const spy = vi.spyOn(api, 'updateWorklogEntries').mockResolvedValue(saved)
    const ok = await useWorklogDayStore.getState().updateEntry(11, { note: '改過' })
    expect(ok).toBe(true)
    expect(spy).toHaveBeenCalledWith(DATE, [{ id: 11, note: '改過' }])
    const state = useWorklogDayStore.getState()
    expect(state.day?.entries[0].note).toBe('改過')
    expect(state.savingIds).toEqual([])
    expect(loadWeek).toHaveBeenCalled()
  })

  it('失敗時回 false、該卡記下錯誤、day 不變', async () => {
    await openReady()
    vi.spyOn(api, 'updateWorklogEntries').mockRejectedValue(
      new ApiError(409, 'WORKLOG_ALREADY_SUBMITTED', '已送出', null, { submitted_count: 1, force_allowed: false }),
    )
    const ok = await useWorklogDayStore.getState().updateEntry(11, { note: '改不動' })
    expect(ok).toBe(false)
    const state = useWorklogDayStore.getState()
    expect(state.entryErrors[11]).toBeTruthy()
    expect(state.day?.entries[0].note).toBe('修正匯出逾時')
    expect(state.savingIds).toEqual([])
  })

  it('🔴 PUT 串成一條：前一個沒回來，下一個不發（撞上後端的送出鎖會 409）', async () => {
    await openReady()
    const first = deferred<WorklogDay>()
    const spy = vi
      .spyOn(api, 'updateWorklogEntries')
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(async () => day({ entries: [entry({ note: '第二次' })] }))
    const a = useWorklogDayStore.getState().updateEntry(11, { note: '第一次' })
    const b = useWorklogDayStore.getState().updateEntry(11, { hours: '3.00' })
    await flush()
    expect(spy).toHaveBeenCalledTimes(1)
    expect(useWorklogDayStore.getState().savingIds).toEqual([11, 11])
    first.resolve(day({ entries: [entry({ note: '第一次' })] }))
    await Promise.all([a, b])
    expect(spy).toHaveBeenCalledTimes(2)
    expect(useWorklogDayStore.getState().day?.entries[0].note).toBe('第二次')
  })

  it('PUT 回來時已換到別天：照送但不拿來畫', async () => {
    await openReady()
    const pending = deferred<WorklogDay>()
    const spy = vi.spyOn(api, 'updateWorklogEntries').mockImplementation(() => pending.promise)
    const saving = useWorklogDayStore.getState().updateEntry(11, { note: '換日前改的' })
    vi.spyOn(api, 'worklogDay').mockResolvedValue(day({ work_date: OTHER, entries: [entry({ id: 99 })] }))
    useWorklogDayStore.getState().open(OTHER)
    await flush()
    pending.resolve(day({ entries: [entry({ note: '換日前改的' })] }))
    await saving
    expect(spy).toHaveBeenCalledWith(DATE, [{ id: 11, note: '換日前改的' }])
    expect(useWorklogDayStore.getState().day?.entries.map((e) => e.id)).toEqual([99])
  })
})

// ── 送出 ──────────────────────────────────────────────────────────────

describe('worklogDay store：submit', () => {
  it('成功：summary 另存、重讀 GET、刷新週欄', async () => {
    await openReady()
    const submit = vi
      .spyOn(api, 'submitWorklog')
      .mockResolvedValue({ work_date: DATE, status: 'submitted', entries: [], summary: summary() })
    const get = vi.spyOn(api, 'worklogDay').mockResolvedValue(day({ entries: [entry({ state: 'submitted', zplanner_worklog_id: 9931 })] }))
    await useWorklogDayStore.getState().submit()
    expect(submit).toHaveBeenCalledTimes(1)
    expect(submit.mock.calls[0][0]).toBe(DATE)
    expect(get).toHaveBeenCalled()
    const state = useWorklogDayStore.getState()
    expect(state.submitSummary?.submitted_count).toBe(1)
    expect(state.day?.entries[0].state).toBe('submitted')
    expect(state.submitting).toBe(false)
  })

  it('AC-11：409 WORKLOG_SUBMIT_IN_PROGRESS 記成 submitError，且不自動重試', async () => {
    await openReady()
    const submit = vi
      .spyOn(api, 'submitWorklog')
      .mockRejectedValue(new ApiError(409, 'WORKLOG_SUBMIT_IN_PROGRESS', '正在送', null, { work_date: DATE }))
    await useWorklogDayStore.getState().submit()
    await new Promise((r) => setTimeout(r, 50))
    expect(submit).toHaveBeenCalledTimes(1)
    const state = useWorklogDayStore.getState()
    expect(state.submitError?.code).toBe('WORKLOG_SUBMIT_IN_PROGRESS')
    expect(state.submitting).toBe(false)
  })

  it('🔴 有未確認欄位時不送（D-16）', async () => {
    await openReady()
    const submit = vi.spyOn(api, 'submitWorklog')
    useWorklogDayStore.getState().setDirty(11, true)
    await useWorklogDayStore.getState().submit()
    expect(submit).not.toHaveBeenCalled()
    expect(useWorklogDayStore.getState().submitError).not.toBeNull()
  })

  it('先等排隊中的 PUT 落地才送（submit 讀的是 DB）', async () => {
    await openReady()
    const put = deferred<WorklogDay>()
    vi.spyOn(api, 'updateWorklogEntries').mockImplementation(() => put.promise)
    const submit = vi
      .spyOn(api, 'submitWorklog')
      .mockResolvedValue({ work_date: DATE, status: 'submitted', entries: [], summary: summary() })
    vi.spyOn(api, 'worklogDay').mockResolvedValue(day())
    const saving = useWorklogDayStore.getState().updateEntry(11, { note: '最後一刻改的' })
    const submitting = useWorklogDayStore.getState().submit()
    await flush()
    expect(submit).not.toHaveBeenCalled()
    put.resolve(day())
    await Promise.all([saving, submitting])
    expect(submit).toHaveBeenCalledTimes(1)
  })
})

// ── 純函式 ────────────────────────────────────────────────────────────

describe('selectToAssignment／assignmentValue（AC-3）', () => {
  const cands = [candidate(), candidate({ issue_id: 502, issue_key: 'PEI-140', project_id: 43, project_key: 'OPS' })]

  it('🔴 Base UI 清除給的 null／空字串 → 不動（不是 0、也不是清成未指派）', () => {
    expect(selectToAssignment(null, cands)).toBeUndefined()
    expect(selectToAssignment(undefined, cands)).toBeUndefined()
    expect(selectToAssignment('', cands)).toBeUndefined()
  })

  it('明確選了未指派哨兵 → 四欄全 null', () => {
    expect(selectToAssignment(UNASSIGNED, cands)).toEqual({
      issue_id: null,
      issue_key: null,
      project_id: null,
      project_key: null,
    })
  })

  it('正對照：選候選 → 四欄一起給（D-12）', () => {
    expect(selectToAssignment('502', cands)).toEqual({
      issue_id: 502,
      issue_key: 'PEI-140',
      project_id: 43,
      project_key: 'OPS',
    })
  })

  it('認不出的值 → 不動，絕不猜', () => {
    expect(selectToAssignment('0', cands)).toBeUndefined()
    expect(selectToAssignment('9999', cands)).toBeUndefined()
  })

  it('哨兵是非數字字串（Number(哨兵) 不是 0）', () => {
    expect(Number.isNaN(Number(UNASSIGNED))).toBe(true)
  })

  it('未指派的 entry 顯示哨兵，候選排第一也不預選（ADR-0008）', () => {
    expect(assignmentValue(entry({ candidates: cands }))).toBe(UNASSIGNED)
    expect(assignmentValue(entry({ issue_id: 502, issue_key: 'PEI-140' }))).toBe('502')
  })
})

describe('readForceDetail', () => {
  it('認得的守門 detail → 筆數與 keys', () => {
    expect(readForceDetail({ assigned_count: 2, assigned_issue_keys: ['A-1', 3, 'B-2'], force_allowed: true })).toEqual({
      assignedCount: 2,
      assignedIssueKeys: ['A-1', 'B-2'],
    })
  })

  it('少任一條件就認不出（不講猜的數字）', () => {
    expect(readForceDetail(null)).toBeNull()
    expect(readForceDetail({ assigned_count: 2 })).toBeNull()
    expect(readForceDetail({ assigned_count: 2, force_allowed: false })).toBeNull()
    expect(readForceDetail({ assigned_count: '2', force_allowed: true })).toBeNull()
    expect(readForceDetail({ assigned_count: 1.5, force_allowed: true })).toBeNull()
  })
})

describe('submitBlockReason（D-16）', () => {
  const base = { phase: { kind: 'ready' } as const, day: day(), savingIds: [], dirtyIds: [], submitting: false }

  it('正對照：乾淨的 ready 狀態可以送', () => {
    expect(submitBlockReason(base)).toBeNull()
  })

  it('有髒值、PUT 在飛、送出中、沒有草稿、全部已送出時都停用', () => {
    expect(submitBlockReason({ ...base, dirtyIds: [11] })).not.toBeNull()
    expect(submitBlockReason({ ...base, savingIds: [11] })).not.toBeNull()
    expect(submitBlockReason({ ...base, submitting: true })).not.toBeNull()
    expect(submitBlockReason({ ...base, phase: { kind: 'absent' }, day: null })).not.toBeNull()
    expect(
      submitBlockReason({ ...base, day: day({ entries: [entry({ zplanner_worklog_id: 9931, state: 'submitted' })] }) }),
    ).not.toBeNull()
  })
})

describe('送出結果的顯示', () => {
  it('AC-11：not_attempted_count > 0 多一行提示，且帶得出筆數', () => {
    const plain = submitSummaryLines(summary())
    const partial = submitSummaryLines(summary({ not_attempted_count: 3 }))
    expect(partial.length).toBe(plain.length + 1)
    expect(partial.some((line) => line.includes('3'))).toBe(true)
  })

  it('IN_PROGRESS 有專屬說法，不是原樣轉述後端句子', () => {
    const err = { code: 'WORKLOG_SUBMIT_IN_PROGRESS', message: '後端原句', detail: null }
    expect(submitErrorText(err)).not.toBe('後端原句')
    // 正對照：其他錯誤照後端的句子
    expect(submitErrorText({ ...err, code: 'ZPLANNER_UNAVAILABLE' })).toBe('後端原句')
  })

  it('未指派／缺時數的 400 講得出兩個數字', () => {
    const text = submitErrorText({
      code: 'INVALID_PARAMETER',
      message: '後端原句',
      detail: { unassigned_count: 2, missing_hours_count: 1 },
    })
    expect(text).toContain('2')
    expect(text).toContain('1')
    expect(text).not.toBe('後端原句')
  })
})

describe('tzOffsetFor', () => {
  it('回 ±HH:MM 形狀', () => {
    expect(tzOffsetFor(DATE)).toMatch(/^[+-]\d{2}:\d{2}$/)
  })
})
