import { create } from 'zustand'
import { ApiError, api, errorMessage, streamUrls } from '@/lib/api'
import { streamSse } from '@/lib/sse'
import type {
  WorklogAllocation,
  WorklogDay,
  WorklogDayEntry,
  WorklogEntryUpdate,
  WorklogSseChunk,
  WorklogSseDone,
  WorklogSseMeta,
  WorklogSubmitSummary,
} from '@/lib/types'
import { useWorklogStore } from '@/store/worklog'

/**
 * 工時草稿的**單日**狀態（中欄 `WorklogDayWorkspace` 用）。
 *
 * ### 為什麼另開一個 store，不擴進 `worklog.ts`（D-15）
 *
 * `worklog.ts` 管週檢視，它的 selector 被週欄訂閱。單日的串流進度、髒值、
 * 送出狀態每秒都在變，擴進去會讓週欄跟著重畫，而且兩邊的狀態欄位會開始
 * 互相引用——之後誰改一邊都得讀懂兩邊。依賴方向刻意是**單向**：這裡在寫入
 * 成功後呼叫 `useWorklogStore.getState().loadWeek()` 刷新週欄的筆數與狀態，
 * `worklog.ts` 永遠不 import 這個檔。
 *
 * ### 只放「目前這一天」
 *
 * 換日期就整份重置、中止串流。**被否決的做法：`Record<date, …>` 多天並存**
 * ——Viewer 一次只看一天，多天並存只會多出「哪一天在串流」這種要判斷的東西。
 *
 * ### 🔴 每個非同步結果回來都要先比對日期
 *
 * 連點左欄的兩天時，第一天的 GET／PUT／串流事件可能在第二天已經顯示之後
 * 才抵達。照單全收的話，畫面會標著 9/14、卡片卻是 9/13 的——然後 Viewer
 * 在上面選專案，PUT 送去的是 9/14 的 URL 帶 9/13 的 entry id（後端會擋成
 * 400，但畫面已經錯了）。所以每一個 await 之後都先確認 `date` 沒變。
 *
 * ### 刻意不沿用 `draft.ts` 的布林組合
 *
 * `streaming` + `error` + `raw` 湊出狀態的寫法，在這裡會出現「串流中又同時
 * 載入失敗」這種不存在的組合。`phase` 是互斥的聯集；會與 phase 並存的事
 * （某幾張卡存檔中、送出中）另外用 `savingIds`／`submitting` 表達。
 */

/** 中欄這一天現在是什麼狀態。互斥：同一時間只會是其中一種。 */
export type WorklogDayPhase =
  /** 沒有選日期 */
  | { kind: 'idle' }
  /** 第一次讀這一天（還沒有任何資料可以畫） */
  | { kind: 'loading' }
  /** 這一天沒有草稿（404 `WORKLOG_DRAFT_NOT_FOUND`）——多數日子的正常狀態 */
  | { kind: 'absent' }
  /**
   * 讀取失敗，**不知道**有沒有草稿。
   *
   * 🔴 不可以併進 `absent`：把網路錯誤畫成「還沒有草稿」，Viewer 會按下產生，
   * 而重產會把對不上新分段的已選單號清空。
   */
  | { kind: 'load-failed'; message: string }
  /** 產生草稿的串流進行中 */
  | { kind: 'streaming' }
  /** 有草稿，`day` 可以畫 */
  | { kind: 'ready' }

/** 串流進行中收到的東西（只用來畫進度，不拿來組卡片——卡片一律來自 GET）。 */
export interface WorklogStreamProgress {
  meta: WorklogSseMeta | null
  chunks: WorklogSseChunk[]
}

/**
 * 重產時因分段改變而清空的歸屬（done 事件的 `attributions_cleared`）。
 *
 * 2026-10-01 Viewer 決定：對不上新分段的已選單號**照規格留空、不擋**，重產照常完成，
 * 事後在畫面上方列出清掉了哪幾張單。以前的「先擋下來、確認後帶 force 重送」整段已移除。
 * 只在那次回應出現、不落地（同 `allocation`）：重新選好之後就不該再說「已清空」。
 */
export interface WorklogClearedAttributions {
  count: number
  issueKeys: string[]
}

/** 帶 code 與 detail 的錯誤，畫面要依 code 分支（例如「正在送出中」）。 */
export interface WorklogDayError {
  code: string
  message: string
  detail: Record<string, unknown> | null
}

/** PUT 單筆時可以帶的欄位（`id` 由 store 補上）。 */
export type WorklogEntryPatch = Omit<WorklogEntryUpdate, 'id'>

interface WorklogDayState {
  /** `YYYY-MM-DD`；`null` ＝沒有選日期 */
  date: string | null
  phase: WorklogDayPhase
  /** 這一天的草稿（GET／PUT 回應）。entries 的**唯一來源**（D-10）。 */
  day: WorklogDay | null
  /**
   * 已經有 `day` 時重讀失敗的訊息。
   *
   * 有草稿時重讀失敗**保留舊的 `day`**，只在畫面上標「可能不是最新」並給重讀鈕
   * ——把已經落地的草稿畫成不存在，比顯示一份稍舊的更糟。
   */
  reloadError: string | null
  stream: WorklogStreamProgress
  /** 產生草稿失敗（`NO_EVIDENCE`、已送出守門的 409…） */
  generateError: WorklogDayError | null
  /** 最近一次重產清空了哪些歸屬。`null`＝這次沒有清空任何一筆（或還沒重產過） */
  clearedAttributions: WorklogClearedAttributions | null
  /** done 事件的 `allocation`，只在那次回應出現，所以另存暫態（D-10） */
  allocation: WorklogAllocation | null
  /** PUT 排隊中或在飛的 entry id。同一筆排了兩次就出現兩次。 */
  savingIds: number[]
  /** 卡片上有 Viewer 打了字、還沒確認（blur／Enter）的欄位的 entry id */
  dirtyIds: number[]
  /** PUT 失敗的訊息，依 entry id。卡片保留 Viewer 的輸入並顯示這句。 */
  entryErrors: Record<number, string>
  submitting: boolean
  /** submit 回應的 `summary`，只在那次回應出現，另存暫態（D-10） */
  submitSummary: WorklogSubmitSummary | null
  submitError: WorklogDayError | null

  /** 換到某一天（或 `null`）。同一天重複呼叫不做事。 */
  open: (date: string | null) => void
  /** 重讀目前這一天（重試鈕用）。 */
  reload: () => Promise<void>
  /**
   * 產生（或重新產生）這一天的草稿。
   *
   * ⚠️ 「重新分配時數」走的也是這支：後端沒有「只重算時數」的端點，所以
   * 它其實是**整次重產**——重掃 git、重打 ZPlanner。同一段的歸屬會保留；對不到
   * 新分段的照規格留空，清掉了哪些記在 `clearedAttributions`（畫面文案要講清楚）。
   */
  generate: (options: { totalHours: number | null }) => Promise<void>
  /** 中止串流（元件卸載時）。 */
  abort: () => void
  /** 存一筆的某幾個欄位。回傳是否成功，讓卡片決定要不要保留輸入。 */
  updateEntry: (id: number, patch: WorklogEntryPatch) => Promise<boolean>
  setDirty: (id: number, dirty: boolean) => void
  /** 把整天送進 ZPlanner（二次確認由畫面負責）。 */
  submit: () => Promise<void>
  reset: () => void
}

// ── 模組層的非 state 變數 ──────────────────────────────────────
// 沿用 `store/draft.ts` 的慣例：AbortController 不放進 zustand state——
// 不能序列化，換掉它也不該觸發重畫。

let controller: AbortController | null = null

/**
 * PUT 的排隊尾端。**所有 PUT 串成一條，一次只有一個在飛。**
 *
 * 🔴 不排隊會出兩種事：
 * 1. 後端把 PUT 包在送出鎖底下（D-14），同一天兩個 PUT 撞在一起，第二個
 *    會拿到 409 `WORKLOG_SUBMIT_IN_PROGRESS`——Viewer 只是連改兩張卡而已。
 * 2. PUT 回應是整份草稿、前端整份取代（D-10）。兩個並行的 PUT 若亂序抵達，
 *    晚到的那份可能是在另一筆寫入**之前**讀的，畫面就會把剛存好的值蓋回舊值，
 *    而 DB 裡其實是新的——下次重整才「自己變回來」。
 * 排隊之後每一份回應都包含了前面所有寫入，照抵達順序取代就一定對。
 */
let putQueue: Promise<unknown> = Promise.resolve()

/**
 * 讀取世代。每次開始一個會決定 `day` 的動作（換日、重讀、產生、PUT 成功）
 * 就遞增；GET 回來時世代不符就丟棄。
 *
 * 只比對日期不夠：同一天裡「重讀」與「產生」可以交錯——產生已經開始了，
 * 早先發出的 GET 才回來，照單全收會把 phase 從 streaming 改成 ready，
 * 串流進度就從畫面上消失了。
 */
let loadGeneration = 0

const emptyStream = (): WorklogStreamProgress => ({ meta: null, chunks: [] })

const initialDayState = {
  phase: { kind: 'idle' } as WorklogDayPhase,
  day: null,
  reloadError: null,
  stream: emptyStream(),
  generateError: null,
  clearedAttributions: null as WorklogClearedAttributions | null,
  allocation: null,
  savingIds: [] as number[],
  dirtyIds: [] as number[],
  entryErrors: {} as Record<number, string>,
  submitting: false,
  submitSummary: null,
  submitError: null,
}

/** 寫入成功後刷新週欄（筆數、未選專案數、送出狀態）。失敗由週欄自己顯示，這裡不處理。 */
function refreshWeek(): void {
  void useWorklogStore.getState().loadWeek()
}

function toDayError(err: unknown): WorklogDayError {
  if (err instanceof ApiError) return { code: err.code, message: err.message, detail: err.detail }
  return { code: 'UNEXPECTED_ERROR', message: errorMessage(err), detail: null }
}

function removeOne(ids: number[], id: number): number[] {
  const index = ids.indexOf(id)
  return index === -1 ? ids : [...ids.slice(0, index), ...ids.slice(index + 1)]
}

/**
 * done 事件的 `attributions_cleared` → 畫面要列的東西。認不出形狀、或 0 筆 → `null`。
 *
 * 認不出來時**不顯示**而不是顯示「0 段」：寧可不說，也不要講一個猜的數字。
 */
export function readClearedAttributions(raw: unknown): WorklogClearedAttributions | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const { count, issue_keys: keys } = raw as Record<string, unknown>
  if (typeof count !== 'number' || !Number.isInteger(count) || count < 1) return null
  const issueKeys = Array.isArray(keys) ? keys.filter((k): k is string => typeof k === 'string') : []
  return { count, issueKeys }
}

export const useWorklogDayStore = create<WorklogDayState>((set, get) => {
  /**
   * 讀某一天的草稿並決定 phase。
   *
   * `expectExists`：呼叫端**確知**草稿已經落地（剛收到 done、剛送出完）。
   * 這時連 404 都不畫成 `absent`——那代表讀取與事實矛盾，該讓 Viewer 重讀，
   * 而不是看到「還沒有草稿」然後再產一次。
   */
  const loadDay = async (target: string, { expectExists = false } = {}): Promise<void> => {
    const generation = ++loadGeneration
    const hadDay = get().day !== null
    if (!hadDay) set({ phase: { kind: 'loading' } })
    const stillMine = () => get().date === target && loadGeneration === generation
    try {
      const day = await api.worklogDay(target)
      if (!stillMine()) return
      set({ day, phase: { kind: 'ready' }, reloadError: null })
    } catch (err) {
      if (!stillMine()) return
      const notFound = err instanceof ApiError && err.code === 'WORKLOG_DRAFT_NOT_FOUND'
      if (notFound && !expectExists) {
        set({ day: null, phase: { kind: 'absent' }, reloadError: null })
      } else if (hadDay) {
        set({ phase: { kind: 'ready' }, reloadError: errorMessage(err) })
      } else {
        set({ phase: { kind: 'load-failed', message: errorMessage(err) } })
      }
    }
  }

  return {
    date: null,
    ...initialDayState,

    open: (date) => {
      if (date === get().date) return
      controller?.abort()
      controller = null
      loadGeneration++
      // 在飛的 PUT **不取消**：那是 Viewer 已經確認過的修改，讓它在後端落地；
      // 回應回來時日期不符，只是不拿來畫而已。
      set({ ...initialDayState, stream: emptyStream(), date, phase: date ? { kind: 'loading' } : { kind: 'idle' } })
      if (date) void loadDay(date)
    },

    reload: async () => {
      const target = get().date
      if (!target || get().phase.kind === 'streaming') return
      await loadDay(target)
    },

    generate: async ({ totalHours }) => {
      const target = get().date
      if (!target) return
      controller?.abort()
      const mine = new AbortController()
      controller = mine
      loadGeneration++ // 作廢在飛的 GET，見 loadGeneration 的註解

      set({
        phase: { kind: 'streaming' },
        stream: emptyStream(),
        generateError: null,
        clearedAttributions: null,
        allocation: null,
        submitSummary: null,
        submitError: null,
      })

      // 換了日期或被中止之後，任何晚到的事件都不准碰 state
      const alive = () => !mine.signal.aborted && get().date === target
      let outcome: 'done' | 'error' | null = null

      const body: Record<string, unknown> = { tz_offset: tzOffsetFor(target) }
      // 省略＝不分配。刻意不送 `null`：省略與 null 在後端同義，但「沒帶」
      // 才是契約寫的語意，少一個要解釋的組合。
      if (totalHours !== null) body.total_hours = totalHours

      await streamSse<WorklogSseMeta, WorklogSseChunk, WorklogSseDone>(
        streamUrls.worklogDraft(target),
        body,
        {
          onMeta: (meta) => {
            if (alive()) set((state) => ({ stream: { ...state.stream, meta } }))
          },
          onChunk: (chunk) => {
            if (alive()) set((state) => ({ stream: { ...state.stream, chunks: [...state.stream.chunks, chunk] } }))
          },
          onDone: (done) => {
            if (!alive()) return
            outcome = 'done'
            set({
              allocation: done.allocation ?? null,
              clearedAttributions: readClearedAttributions(done.attributions_cleared),
            })
          },
          onError: (event) => {
            if (!alive()) return
            outcome = 'error'
            set({ generateError: { code: event.code, message: event.message, detail: event.detail ?? null } })
          },
        },
        mine.signal,
      ).catch((err) => {
        // streamSse 只在 401 時 throw（其餘錯誤都轉成 onError）
        if (!alive()) return
        outcome = 'error'
        set({ generateError: toDayError(err) })
      })

      if (controller === mine) controller = null
      if (!alive()) return

      if (outcome === 'done') {
        // D-10：卡片一律來自 GET，不從 done 的 entries 組。草稿確定已落地，
        // 所以這次讀取連 404 都不准畫成「沒有草稿」。
        await loadDay(target, { expectExists: true })
        refreshWeek()
        return
      }
      if (outcome === null) {
        // 串流自然結束卻沒有 done 也沒有 error：不知道草稿落地了沒有
        set({
          generateError: {
            code: 'STREAM_ENDED',
            message: '串流意外結束，沒有收到完成訊號。已重新讀取這一天，請確認草稿是否已經產生。',
            detail: null,
          },
        })
      }
      // 失敗後一律重讀，不自己推斷：串流中途的錯誤（例如重產時 entries 被拒）
      // 可能已經換掉了 meta，只有 GET 講得出現在 DB 裡是什麼。
      await loadDay(target)
    },

    abort: () => {
      const wasStreaming = get().phase.kind === 'streaming'
      controller?.abort()
      controller = null
      // 中止之後不知道後端寫到哪裡了（串流中斷不等於沒落地），重讀一次問事實
      const target = get().date
      if (wasStreaming && target) void loadDay(target)
    },

    updateEntry: (id, patch) => {
      const target = get().date
      if (!target) return Promise.resolve(false)
      set((state) => {
        const { [id]: _cleared, ...rest } = state.entryErrors
        return { savingIds: [...state.savingIds, id], entryErrors: rest }
      })

      const run = async (): Promise<boolean> => {
        // 排隊期間 Viewer 可能已經換到別天，這筆**照送**：那是他確認過的修改，
        // 換日不代表撤回。只是回應回來時日期不符，不拿來畫。
        try {
          const day = await api.updateWorklogEntries(target, [{ id, ...patch }])
          if (get().date !== target) return true
          loadGeneration++ // 比這份回應早發出的 GET 一律作廢
          set({ day, reloadError: null })
          refreshWeek()
          return true
        } catch (err) {
          if (get().date === target) {
            set((state) => ({ entryErrors: { ...state.entryErrors, [id]: errorMessage(err) } }))
          }
          return false
        } finally {
          if (get().date === target) set((state) => ({ savingIds: removeOne(state.savingIds, id) }))
        }
      }

      const result = putQueue.then(run, run)
      putQueue = result.catch(() => undefined)
      return result
    },

    setDirty: (id, dirty) =>
      set((state) => {
        const has = state.dirtyIds.includes(id)
        if (dirty === has) return state
        return { dirtyIds: dirty ? [...state.dirtyIds, id] : state.dirtyIds.filter((x) => x !== id) }
      }),

    submit: async () => {
      const target = get().date
      if (!target || get().submitting) return
      set({ submitting: true, submitError: null, submitSummary: null })
      // 🔴 先等排隊中的 PUT 全部落地：submit 讀的是 DB，畫面上剛改的值若還在
      // 路上，送進 ZPlanner 的就是 Viewer 沒看到的舊值（D-16）。
      await putQueue
      if (get().date !== target) return
      // 送出鈕在有髒值時本來就停用；這裡再擋一次，是因為「按下送出」與「欄位
      // blur 觸發 PUT」發生在同一次點擊裡，順序由瀏覽器決定。PUT 失敗的那張卡
      // 也會留在 dirtyIds（輸入保留、與 DB 不同），同樣不准送。
      if (get().dirtyIds.length > 0) {
        set({
          submitting: false,
          submitError: {
            code: 'UNSAVED_FIELDS',
            message: '還有欄位沒存好（或存檔失敗），整批沒有送出。處理完卡片上的錯誤再送。',
            detail: null,
          },
        })
        return
      }
      try {
        const res = await api.submitWorklog(target)
        if (get().date !== target) return
        set({ submitSummary: res.summary })
        // submit 回應的 entries 沒有 cluster_key／label／reason，不拿它畫（D-10）
        await loadDay(target, { expectExists: true })
        refreshWeek()
      } catch (err) {
        if (get().date !== target) return
        // 🔴 409 WORKLOG_SUBMIT_IN_PROGRESS **不自動重試**：自動重試正是那道鎖
        // 要防的動作（兩個重疊的送出會各自以為沒有重複，同一筆送兩次）。
        set({ submitError: toDayError(err) })
      } finally {
        if (get().date === target) set({ submitting: false })
      }
    },

    reset: () => {
      controller?.abort()
      controller = null
      loadGeneration++
      set({ date: null, ...initialDayState, stream: emptyStream() })
    },
  }
})

// ── 純函式（state → 顯示）────────────────────────────────────────
// 放在 store 而不是元件裡：沒有 React 也測得了，而且幾個元件共用同一套說法。

/**
 * 某一天的本地時區偏移（`+08:00`）。
 *
 * ⚠️ **要用「所選那一天」的偏移，不是「現在」的。** 有夏令時間的地方，三月
 * 回頭補一月的工時時，現在的偏移與那天差一小時——日界就錯一小時，半夜那筆
 * commit 會跑到隔壁那天。取當天中午：避開午夜附近的切換點。
 */
export function tzOffsetFor(date: string): string {
  const [y, m, d] = date.split('-').map(Number)
  const minutesEast = -new Date(y, m - 1, d, 12).getTimezoneOffset()
  const sign = minutesEast >= 0 ? '+' : '-'
  const abs = Math.abs(minutesEast)
  return `${sign}${String(Math.floor(abs / 60)).padStart(2, '0')}:${String(abs % 60).padStart(2, '0')}`
}

/**
 * 總時數輸入框 → 要送的值。空白 ＝ `null`（不分配）；看不懂 ＝ `undefined`。
 *
 * 看不懂時回 `undefined` 而不是 `null`：兩者在呼叫端的意思完全不同——`null`
 * 會產出一份沒有時數的草稿，Viewer 打錯字卻得到「成功」。
 */
export function parseTotalHours(input: string): number | null | undefined {
  const text = input.trim()
  if (text === '') return null
  if (!/^\d+(\.\d+)?$/.test(text)) return undefined
  const value = Number(text)
  return Number.isFinite(value) && value > 0 ? value : undefined
}

/** 這筆已經送進 ZPlanner（有 worklog id）。這種筆不能改——PUT 會 409（D-21）。 */
export function isEntryLocked(entry: WorklogDayEntry): boolean {
  return entry.zplanner_worklog_id !== null && entry.zplanner_worklog_id !== undefined
}

/** 這筆 Viewer 選過專案了。判準同重產守門：`issue_id` 或 `issue_key` 任一有值。 */
export function isEntryAssigned(entry: WorklogDayEntry): boolean {
  return entry.issue_id !== null || entry.issue_key !== null
}

export type WorklogEntryTone = 'neutral' | 'ok' | 'caution' | 'danger'

/**
 * 一筆的送出狀態 → 顯示文字。五態各有一句，混合狀態才畫得出來（AC-10）。
 *
 * 刻意不寫成 exhaustive switch：`state` 是後端字串，多一個值時要安全地畫成
 * 看得到的「未知狀態」，而不是編譯失敗或空白。
 */
export function entryStateText(entry: WorklogDayEntry): {
  label: string
  tone: WorklogEntryTone
  hint: string | null
} {
  switch (entry.state) {
    case 'pending':
      return { label: '未送出', tone: 'neutral', hint: null }
    case 'submitted':
      return {
        label: '已送出',
        tone: 'ok',
        hint: entry.is_locked ? 'ZPlanner 那邊已鎖定，要改得走更正申請。' : null,
      }
    case 'skipped':
      return {
        label: '已略過（重複）',
        tone: 'caution',
        hint: 'ZPlanner 這天已經有同一個 issue、同樣時數的工時，所以這筆沒有送。',
      }
    case 'failed':
      return {
        label: '送出失敗',
        tone: 'danger',
        hint: entry.last_error_message ?? '沒有收到失敗原因。',
      }
    case 'unknown':
      // ⚠️ 不是失敗：不知道送進去沒有。請人去對帳，不要叫他重送。
      return {
        label: '狀態不明',
        tone: 'caution',
        hint: '不確定有沒有寫進 ZPlanner。先到 ZPlanner 查這一天的工時再決定，不要直接重送。',
      }
    default:
      return { label: `未知狀態（${String(entry.state)}）`, tone: 'caution', hint: null }
  }
}

/** 送出區要顯示的合計。只算還沒送進 ZPlanner 的筆。 */
export function daySubmitTotals(day: WorklogDay): {
  /** 還沒有 worklog id 的筆數 */
  pendingCount: number
  /** 這些筆的時數合計（`"7.50"`；有筆沒時數時只加有的那些） */
  pendingHours: string
  unassignedCount: number
  missingHoursCount: number
} {
  const pending = day.entries.filter((e) => !isEntryLocked(e))
  let total = 0
  let missingHours = 0
  for (const entry of pending) {
    const value = entry.hours === null ? Number.NaN : Number.parseFloat(entry.hours)
    if (Number.isFinite(value)) total += value
    else missingHours += 1
  }
  return {
    pendingCount: pending.length,
    pendingHours: total.toFixed(2),
    unassignedCount: pending.filter((e) => e.issue_id === null).length,
    missingHoursCount: missingHours,
  }
}

/**
 * 送出鈕為什麼不能按。`null` ＝可以按。
 *
 * 🔴 有未確認的欄位或 PUT 在飛時一律停用（D-16）：submit 讀的是 DB，畫面與
 * DB 之間的空窗會把 Viewer 沒看到的舊值送進 ZPlanner。
 */
export function submitBlockReason(state: {
  phase: WorklogDayPhase
  day: WorklogDay | null
  savingIds: number[]
  dirtyIds: number[]
  submitting: boolean
}): string | null {
  if (state.phase.kind !== 'ready' || !state.day) return '還沒有可以送出的草稿。'
  if (state.submitting) return '正在送出中。'
  if (state.dirtyIds.length > 0) return '還有欄位沒存好：按 Enter 或點到別處確認後再送出。'
  if (state.savingIds.length > 0) return '正在存檔，等存完再送出。'
  if (daySubmitTotals(state.day).pendingCount === 0) return '這一天的每一筆都已經送進 ZPlanner 了。'
  return null
}

/** 「有 N 段因分段改變，歸屬已清空：PEI-133、…」。單號是資料，原樣列出。 */
export function clearedAttributionsText(cleared: WorklogClearedAttributions): string {
  const keys = cleared.issueKeys.length > 0 ? `：${cleared.issueKeys.join('、')}` : ''
  return `有 ${cleared.count} 段因分段改變，歸屬已清空${keys}`
}

/**
 * submit 失敗 → 顯示文字。
 *
 * - `WORKLOG_SUBMIT_IN_PROGRESS`：暫時狀態，等它跑完。**不要叫 Viewer 再按一次**。
 * - 400 且 detail 有未指派／缺時數的筆數：講出兩個數字，因為補救動作不同
 *   （一個去選專案、一個去輸入總時數）。detail 認不出來就只講後端的句子。
 */
export function submitErrorText(error: WorklogDayError): string {
  if (error.code === 'WORKLOG_SUBMIT_IN_PROGRESS') {
    return '這一天正在送出中。等它跑完再按「重讀」看結果——不要再按一次送出。'
  }
  const d = error.detail
  if (d && typeof d.unassigned_count === 'number' && typeof d.missing_hours_count === 'number') {
    const parts: string[] = []
    if (d.unassigned_count > 0) parts.push(`${d.unassigned_count} 筆還沒選要記到哪張單`)
    if (d.missing_hours_count > 0) parts.push(`${d.missing_hours_count} 筆沒有時數`)
    if (parts.length > 0) return `${parts.join('、')}，整批一筆都沒有送出。補齊後再送。`
  }
  return error.message
}

/**
 * submit 成功後的摘要句子（一行一句）。
 *
 * 🔴 `not_attempted_count > 0` 一定要講、而且要叫 Viewer 再按一次：停送的筆
 * 在 DB 裡與「從未送過」長得一樣，這句話是他唯一看得出「這批沒跑完」的地方。
 */
export function submitSummaryLines(summary: WorklogSubmitSummary): string[] {
  const lines = [
    `已送出 ${summary.submitted_count} 筆、略過 ${summary.skipped_count} 筆、失敗 ${summary.failed_count} 筆、狀態不明 ${summary.unknown_count} 筆（整天合計）。`,
  ]
  if (summary.not_attempted_count > 0) {
    lines.push(
      `還有 ${summary.not_attempted_count} 筆因為這批的時間用完沒有開始送。再按一次「送出到 ZPlanner」把剩下的送完。`,
    )
  }
  for (const dup of summary.skipped_duplicates) lines.push(`第 ${dup.seq} 筆：${dup.reason}`)
  if (summary.orphans.length > 0) {
    lines.push(
      `有 ${summary.orphans.length} 筆已經寫進 ZPlanner，但沒能記回草稿。請直接到 ZPlanner 查這一天的工時，避免重複。`,
    )
  }
  return lines
}
