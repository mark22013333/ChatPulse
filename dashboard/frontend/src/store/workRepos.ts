import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import type { WorkRepo } from '@/lib/types'

/**
 * 「我的工作 repo」的狀態（Draft Worklog，ADR-0008）。
 *
 * 形狀比照 `store/codeProjects.ts`（`loaded` 旗標 ＋ `ensureLoaded()` 去重），
 * 但**是另一份登錄、另一組端點**：那邊是「查程式碼回答問題」，這邊是
 * 「回想這天做了什麼」。兩者的分支語意相反，不要共用。
 */

/**
 * 表單的暫存值。
 *
 * `author_emails` 與 `branch_filter` 在這裡是**使用者打的原文字串**（一行一筆
 * 或用逗號分隔），送出前才由 `parseList()` 拆成陣列。理由：中途打到一半的
 * 「a@b.com,」若立刻拆成陣列再組回字串，游標與逗號會被吃掉。
 */
export interface WorkRepoDraft {
  name: string
  repo_path: string
  author_emails: string
  branch_filter: string
  /** 空字串＝沒有預設 ZPlanner 專案。送出時轉成 `null`（＝清除，不是「不改」）。 */
  default_zplanner_project_id: string
  default_zplanner_project_key: string
  enabled: boolean
}

export const emptyWorkRepoDraft = (): WorkRepoDraft => ({
  name: '',
  repo_path: '',
  author_emails: '',
  branch_filter: '',
  default_zplanner_project_id: '',
  default_zplanner_project_key: '',
  enabled: true,
})

/** 既有的一筆 → 表單暫存值。`seedEmails` 是從 `detected_emails` 點進來的補充。 */
export function draftFromWorkRepo(repo: WorkRepo, seedEmails: string[] = []): WorkRepoDraft {
  return {
    name: repo.name,
    repo_path: repo.repo_path,
    author_emails: formatList([...repo.author_emails, ...seedEmails]),
    branch_filter: formatList(repo.branch_filter),
    default_zplanner_project_id:
      repo.default_zplanner_project_id == null ? '' : String(repo.default_zplanner_project_id),
    default_zplanner_project_key: repo.default_zplanner_project_key ?? '',
    enabled: repo.enabled,
  }
}

/**
 * 把輸入框的原文拆成陣列：換行或逗號分隔，去空白、丟掉空項、**去重且保留順序**。
 *
 * 去重保留順序而不是排序，與後端 `_clean_str_list` 同一個理由：使用者填的
 * 順序有意義（第一個通常是主要信箱），重排會讓他下次打開設定頁看到一份
 * 自己沒填過的清單。
 */
export function parseList(raw: string): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  for (const part of raw.split(/[\n,]/)) {
    const item = part.trim()
    if (!item || seen.has(item)) continue
    seen.add(item)
    out.push(item)
  }
  return out
}

/** 陣列 → 輸入框原文。一行一筆，肉眼數得出來有幾個。 */
export function formatList(items: string[]): string {
  return parseList(items.join('\n')).join('\n')
}

/**
 * 送出前的檢查。回傳 `null` 表示可以送。
 *
 * **這裡只擋「一定是錯的」**：
 * - 作者信箱留空**不擋**——後端允許，而且「先建立、按重新檢查看 `detected_emails`
 *   再回來填」正是文件建議的流程。它的後果由畫面上的警告文字負責講清楚，
 *   不是由這裡擋住。
 * - 分支留空**不擋**——那是預期用法（掃全部分支）。
 */
export function validateWorkRepoDraft(draft: WorkRepoDraft): string | null {
  if (!draft.name.trim()) return '請填寫名稱'
  const path = draft.repo_path.trim()
  if (!path) return '請填寫 repo 路徑'
  // 後端 `_validate_repo_path` 也會擋，但先擋一次省一次往返：相對路徑會相對於
  // 伺服器的工作目錄去解，那跟使用者心裡想的不是同一個地方
  if (!path.startsWith('/') && !/^[A-Za-z]:[\\/]/.test(path) && !path.startsWith('\\\\')) {
    return 'repo 路徑必須是絕對路徑'
  }
  const rawId = draft.default_zplanner_project_id.trim()
  if (rawId && !/^\d+$/.test(rawId)) return 'ZPlanner 專案 ID 必須是數字'
  return null
}

/** 表單暫存值 → 送給後端的 body。 */
function toBody(draft: WorkRepoDraft) {
  const rawId = draft.default_zplanner_project_id.trim()
  return {
    name: draft.name.trim(),
    repo_path: draft.repo_path.trim(),
    author_emails: parseList(draft.author_emails),
    branch_filter: parseList(draft.branch_filter),
    // 一律明確送出（含 `null`）：對 PATCH 而言省略是「不改」、`null` 才是
    // 「清除」，而「這個 repo 沒有預設專案」是使用者會主動選的狀態
    default_zplanner_project_id: rawId ? Number(rawId) : null,
    default_zplanner_project_key: draft.default_zplanner_project_key.trim() || null,
  }
}

interface WorkRepoState {
  repos: WorkRepo[]
  loading: boolean
  saving: boolean
  error: string | null
  /** `load()` 是否已經成功跑過一次。 */
  loaded: boolean
  /** 哪一筆正在跑「重新檢查」。按鈕要照這個鎖住，不然連按會疊出好幾次 git 呼叫。 */
  verifyingId: number | null

  load: () => Promise<void>
  /** 沒載入過才載入，重複呼叫是安全的。要強制重新載入請直接呼叫 `load()`。 */
  ensureLoaded: () => Promise<void>
  create: (draft: WorkRepoDraft) => Promise<WorkRepo | null>
  update: (id: number, draft: WorkRepoDraft) => Promise<WorkRepo | null>
  setEnabled: (id: number, enabled: boolean) => Promise<void>
  remove: (id: number) => Promise<boolean>
  verify: (id: number) => Promise<void>
  clearError: () => void
}

/**
 * 合併一筆更新結果。
 *
 * **`verification` 只在 POST／PATCH／verify 的回覆裡有**，GET 清單沒有。
 * 直接整筆換掉會讓「編輯名稱」把剛才驗證出來的 `detected_emails` 洗掉——
 * 使用者正是為了看那份清單才按的重新檢查。所以沒帶 `verification` 時保留舊的。
 */
function mergeRepo(prev: WorkRepo, next: WorkRepo): WorkRepo {
  return next.verification ? next : { ...next, verification: prev.verification }
}

export const useWorkRepoStore = create<WorkRepoState>((set, get) => ({
  repos: [],
  loading: false,
  saving: false,
  error: null,
  loaded: false,
  verifyingId: null,

  clearError: () => set({ error: null }),

  load: async () => {
    set({ loading: true, error: null })
    try {
      const res = await api.workRepos()
      set({ repos: res.repos, loaded: true })
    } catch (err) {
      set({ error: errorMessage(err) })
    } finally {
      set({ loading: false })
    }
  },

  ensureLoaded: async () => {
    const { loaded, loading } = get()
    if (loaded || loading) return
    await get().load()
  },

  create: async (draft) => {
    const invalid = validateWorkRepoDraft(draft)
    if (invalid) {
      set({ error: invalid })
      return null
    }
    set({ saving: true, error: null })
    try {
      const repo = await api.createWorkRepo(toBody(draft))
      set((s) => ({ repos: [...s.repos, repo] }))
      return repo
    } catch (err) {
      set({ error: errorMessage(err) })
      return null
    } finally {
      set({ saving: false })
    }
  },

  update: async (id, draft) => {
    const invalid = validateWorkRepoDraft(draft)
    if (invalid) {
      set({ error: invalid })
      return null
    }
    set({ saving: true, error: null })
    try {
      const repo = await api.updateWorkRepo(id, { ...toBody(draft), enabled: draft.enabled })
      set((s) => ({ repos: s.repos.map((r) => (r.id === id ? mergeRepo(r, repo) : r)) }))
      return repo
    } catch (err) {
      set({ error: errorMessage(err) })
      return null
    } finally {
      set({ saving: false })
    }
  },

  setEnabled: async (id, enabled) => {
    set({ error: null })
    try {
      const repo = await api.updateWorkRepo(id, { enabled })
      set((s) => ({ repos: s.repos.map((r) => (r.id === id ? mergeRepo(r, repo) : r)) }))
    } catch (err) {
      set({ error: errorMessage(err) })
    }
  },

  remove: async (id) => {
    set({ error: null })
    try {
      await api.deleteWorkRepo(id)
      set((s) => ({ repos: s.repos.filter((r) => r.id !== id) }))
      return true
    } catch (err) {
      set({ error: errorMessage(err) })
      return false
    }
  },

  verify: async (id) => {
    set({ error: null, verifyingId: id })
    try {
      const repo = await api.verifyWorkRepo(id)
      set((s) => ({ repos: s.repos.map((r) => (r.id === id ? mergeRepo(r, repo) : r)) }))
    } catch (err) {
      set({ error: errorMessage(err) })
    } finally {
      set({ verifyingId: null })
    }
  },
}))
