import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import type { CodeEnvironment, CodeProject } from '@/lib/types'

/** 環境的顯示名。與後端 cfg.CODE_ENV_LABELS 對齊，兩邊都用封閉字彙。 */
export const ENV_LABELS: Record<CodeEnvironment, string> = {
  production: '正式環境',
  uat: 'UAT 環境',
  dev: '開發環境',
}

export const ENV_ORDER: CodeEnvironment[] = ['production', 'uat', 'dev']

export interface ProjectDraft {
  name: string
  repo_path: string
  branches: Partial<Record<CodeEnvironment, string>>
  default_env: CodeEnvironment
}

export const emptyDraft = (): ProjectDraft => ({
  name: '',
  repo_path: '',
  branches: {},
  default_env: 'production',
})

/**
 * 專案是否有任何分支對應。沒有分支對應的專案正是「查問題時找錯環境」本身，
 * 後端會擋，前端也先擋一次，讓使用者在按下送出前就知道。
 */
export function hasAnyBranch(draft: ProjectDraft): boolean {
  return ENV_ORDER.some((env) => (draft.branches[env] ?? '').trim() !== '')
}

/** 送出前的檢查。回傳 null 表示可以送。 */
export function validateDraft(draft: ProjectDraft): string | null {
  if (!draft.name.trim()) return '請填寫專案名稱'
  if (!draft.repo_path.trim()) return '請填寫專案路徑'
  if (!hasAnyBranch(draft)) return '至少要指定一個環境的分支（正式／UAT／開發）'
  if (!(draft.branches[draft.default_env] ?? '').trim()) {
    return `預設環境「${ENV_LABELS[draft.default_env]}」沒有對應的分支`
  }
  return null
}

interface CodeProjectState {
  projects: CodeProject[]
  loading: boolean
  saving: boolean
  error: string | null
  /** `load()` 是否已經成功跑過一次。 */
  loaded: boolean

  load: () => Promise<void>
  /**
   * 沒載入過才載入，重複呼叫是安全的。
   *
   * 在此之前 `load()` 完全沒有去重，而它有兩個呼叫端（草稿工作區與參考專案
   * 設定），切一次頁籤就多打一次 API。要強制重新載入請直接呼叫 `load()`。
   */
  ensureLoaded: () => Promise<void>
  create: (draft: ProjectDraft) => Promise<CodeProject | null>
  update: (id: number, draft: ProjectDraft) => Promise<CodeProject | null>
  remove: (id: number) => Promise<boolean>
  verify: (id: number) => Promise<void>
  clearError: () => void
}

/** 把 draft 的分支表清成只留有填的，空字串代表「這個環境不對應任何分支」。 */
function cleanBranches(
  branches: Partial<Record<CodeEnvironment, string>>,
): Partial<Record<CodeEnvironment, string>> {
  const out: Partial<Record<CodeEnvironment, string>> = {}
  for (const env of ENV_ORDER) {
    const v = (branches[env] ?? '').trim()
    if (v) out[env] = v
  }
  return out
}

export const useCodeProjectStore = create<CodeProjectState>((set, get) => ({
  projects: [],
  loading: false,
  saving: false,
  error: null,
  loaded: false,

  clearError: () => set({ error: null }),

  load: async () => {
    set({ loading: true, error: null })
    try {
      const res = await api.codeProjects()
      set({ projects: res.projects, loaded: true })
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
    const invalid = validateDraft(draft)
    if (invalid) {
      set({ error: invalid })
      return null
    }
    set({ saving: true, error: null })
    try {
      const project = await api.createCodeProject({
        name: draft.name.trim(),
        repo_path: draft.repo_path.trim(),
        branches: cleanBranches(draft.branches),
        default_env: draft.default_env,
      })
      set((s) => ({ projects: [...s.projects, project] }))
      return project
    } catch (err) {
      set({ error: errorMessage(err) })
      return null
    } finally {
      set({ saving: false })
    }
  },

  update: async (id, draft) => {
    const invalid = validateDraft(draft)
    if (invalid) {
      set({ error: invalid })
      return null
    }
    set({ saving: true, error: null })
    try {
      const project = await api.updateCodeProject(id, {
        name: draft.name.trim(),
        repo_path: draft.repo_path.trim(),
        branches: cleanBranches(draft.branches),
        default_env: draft.default_env,
      })
      set((s) => ({ projects: s.projects.map((p) => (p.id === id ? project : p)) }))
      return project
    } catch (err) {
      set({ error: errorMessage(err) })
      return null
    } finally {
      set({ saving: false })
    }
  },

  remove: async (id) => {
    set({ error: null })
    try {
      await api.deleteCodeProject(id)
      set((s) => ({ projects: s.projects.filter((p) => p.id !== id) }))
      return true
    } catch (err) {
      set({ error: errorMessage(err) })
      return false
    }
  },

  verify: async (id) => {
    set({ error: null })
    try {
      const project = await api.verifyCodeProject(id)
      set((s) => ({ projects: s.projects.map((p) => (p.id === id ? project : p)) }))
    } catch (err) {
      set({ error: errorMessage(err) })
    }
  },
}))
