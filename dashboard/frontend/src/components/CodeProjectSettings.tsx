import { useEffect, useState } from 'react'
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  FolderGitIcon,
  Loader2Icon,
  PlusIcon,
  RefreshCwIcon,
  Trash2Icon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  ENV_LABELS,
  ENV_ORDER,
  emptyDraft,
  useCodeProjectStore,
  type ProjectDraft,
} from '@/store/codeProjects'
import type { CodeEnvironment, CodeProject } from '@/lib/types'

/**
 * 參考專案設定（ADR-0006）。
 *
 * 這一頁的重點是**分支環境矩陣**：拿 UAT 的程式碼回答正式環境的問題，
 * 會產生看起來有憑有據、實際上錯的答案。所以每個環境對應哪個分支要在這裡
 * 講清楚，而且驗證結果（分支還在不在）直接顯示，不要等到產草稿時才發現。
 */
export function CodeProjectSettings() {
  const { projects, loading, saving, error, load, create, update, remove, verify, clearError } =
    useCodeProjectStore()
  const [draft, setDraft] = useState<ProjectDraft>(emptyDraft)
  const [editingId, setEditingId] = useState<number | null>(null)

  useEffect(() => {
    void load()
  }, [load])

  const resetForm = () => {
    setDraft(emptyDraft())
    setEditingId(null)
    clearError()
  }

  const submit = async () => {
    const saved = editingId ? await update(editingId, draft) : await create(draft)
    if (saved) resetForm()
  }

  const startEdit = (p: CodeProject) => {
    clearError()
    setEditingId(p.id)
    setDraft({
      name: p.name,
      repo_path: p.repo_path,
      branches: { ...p.branches },
      default_env: p.default_env,
    })
  }

  const setBranch = (env: CodeEnvironment, value: string) =>
    setDraft((d) => ({ ...d, branches: { ...d.branches, [env]: value } }))

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <FolderGitIcon className="size-5" aria-hidden />
          參考專案
        </h2>
        <p className="text-sm text-muted-foreground">
          登錄本機的 git repo，Draft Reply 就能引用實際程式碼回答 PM 的問題。
          <strong className="text-foreground">
            務必把正式與 UAT 分別對應到正確的分支
          </strong>
          ——查錯環境會產生看起來有依據、實際上錯的答案。
        </p>
      </header>

      {error && (
        <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}

      {/* ── 專案清單 ─────────────────────────────────────── */}
      {loading ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2Icon className="size-4 animate-spin" aria-hidden />
          載入中…
        </p>
      ) : projects.length === 0 ? (
        <p className="rounded-md border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
          還沒有登錄任何專案。在下面新增一個，草稿就能引用程式碼了。
        </p>
      ) : (
        <ul className="space-y-3">
          {projects.map((p) => (
            <li key={p.id} className="rounded-lg border p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-medium">{p.name}</p>
                  <p className="truncate font-mono text-xs text-muted-foreground">{p.repo_path}</p>
                </div>
                <div className="flex shrink-0 gap-1">
                  <Button size="sm" variant="ghost" onClick={() => void verify(p.id)}>
                    <RefreshCwIcon className="size-3.5" aria-hidden />
                    重新檢查
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => startEdit(p)}>
                    編輯
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`刪除 ${p.name}`}
                    onClick={() => void remove(p.id)}
                  >
                    <Trash2Icon className="size-3.5" aria-hidden />
                  </Button>
                </div>
              </div>

              {/* 分支環境矩陣 —— 這一段是整頁的重點 */}
              <ul className="mt-3 space-y-1 text-sm">
                {ENV_ORDER.filter((env) => p.branches[env]).map((env) => {
                  const info = p.verification?.branches?.[env]
                  const ok = info?.exists ?? true
                  return (
                    <li key={env} className="flex flex-wrap items-center gap-2">
                      <span className="w-20 shrink-0 text-muted-foreground">
                        {ENV_LABELS[env]}
                      </span>
                      <span aria-hidden>→</span>
                      <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                        {p.branches[env]}
                      </code>
                      {env === p.default_env && (
                        <span className="text-xs text-muted-foreground">（預設）</span>
                      )}
                      {info &&
                        (ok ? (
                          <span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                            <CheckCircle2Icon className="size-3.5" aria-hidden />
                            {info.commit}
                            {info.commit_date ? `（${info.commit_date.slice(0, 10)}）` : null}
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
                            <AlertTriangleIcon className="size-3.5" aria-hidden />
                            分支不存在
                            {info.did_you_mean?.length
                              ? `，是不是 ${info.did_you_mean[0]}？`
                              : null}
                          </span>
                        ))}
                    </li>
                  )
                })}
              </ul>

              {p.last_verify_error && (
                <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">
                  {p.last_verify_error}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* ── 新增／編輯表單 ───────────────────────────────── */}
      <div className="space-y-4 rounded-lg border p-4">
        <h3 className="text-sm font-medium">{editingId ? '編輯專案' : '新增專案'}</h3>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="cp-name">專案名稱</Label>
            <Input
              id="cp-name"
              value={draft.name}
              placeholder="智慧客服後端"
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cp-path">本機路徑（絕對路徑）</Label>
            <Input
              id="cp-path"
              value={draft.repo_path}
              placeholder="D:\work\cs-backend"
              onChange={(e) => setDraft((d) => ({ ...d, repo_path: e.target.value }))}
            />
          </div>
        </div>

        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">分支對應</legend>
          <p className="text-xs text-muted-foreground">
            留空表示該環境不對應任何分支。至少要填一個。
          </p>
          {ENV_ORDER.map((env) => (
            <div key={env} className="flex flex-wrap items-center gap-2">
              <Label htmlFor={`cp-branch-${env}`} className="w-20 shrink-0 font-normal">
                {ENV_LABELS[env]}
              </Label>
              <Input
                id={`cp-branch-${env}`}
                className="max-w-xs"
                value={draft.branches[env] ?? ''}
                placeholder={env === 'production' ? 'main' : env === 'uat' ? 'release/uat' : 'develop'}
                onChange={(e) => setBranch(env, e.target.value)}
              />
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <input
                  type="radio"
                  name="cp-default-env"
                  checked={draft.default_env === env}
                  onChange={() => setDraft((d) => ({ ...d, default_env: env }))}
                />
                設為預設
              </label>
            </div>
          ))}
        </fieldset>

        <div className="flex gap-2">
          <Button onClick={() => void submit()} disabled={saving}>
            {saving ? (
              <Loader2Icon className="size-4 animate-spin" aria-hidden />
            ) : (
              <PlusIcon className="size-4" aria-hidden />
            )}
            {editingId ? '儲存' : '新增'}
          </Button>
          {editingId && (
            <Button variant="ghost" onClick={resetForm}>
              取消
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
