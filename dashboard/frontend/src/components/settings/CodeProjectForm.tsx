import { useState } from 'react'
import { Loader2Icon, PlusIcon } from 'lucide-react'
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

/** 每個環境的分支範例。放 placeholder 而不是預填——猜錯分支比沒填更難發現。 */
const BRANCH_PLACEHOLDER: Record<CodeEnvironment, string> = {
  production: 'main',
  uat: 'release/uat',
  dev: 'develop',
}

interface CodeProjectFormProps {
  /** 要編輯的專案；`null` 是新增。 */
  project: CodeProject | null
  /** 送出成功或按取消之後通知呼叫端（讓它把編輯目標清掉）。 */
  onDone: () => void
}

/**
 * 參考專案的新增／編輯表單（規格 §9.1／§9.2）。
 *
 * **draft 住在這裡，不由 `CodeProjectsPage` 傳進來**（規格 §9.3：子元件不接大
 * props 物件）。切換編輯目標時由呼叫端用 `key` 讓這個元件重新掛載，初始值
 * 就跟著 `project` 走——不必在兩層之間同步 setter，也不會出現「換了編輯目標
 * 但表單還留著上一筆的值」這種狀態。
 */
export function CodeProjectForm({ project, onDone }: CodeProjectFormProps) {
  const saving = useCodeProjectStore((s) => s.saving)
  const create = useCodeProjectStore((s) => s.create)
  const update = useCodeProjectStore((s) => s.update)
  const clearError = useCodeProjectStore((s) => s.clearError)

  const [draft, setDraft] = useState<ProjectDraft>(() =>
    project
      ? {
          name: project.name,
          repo_path: project.repo_path,
          branches: { ...project.branches },
          default_env: project.default_env,
        }
      : emptyDraft(),
  )

  const submit = async () => {
    const saved = project ? await update(project.id, draft) : await create(draft)
    // 失敗時**不要**清表單：使用者剛打的東西還在上面，清掉等於叫他重打一次。
    // 錯誤訊息由 store 的 error 承擔，畫在清單頁上方的 role="alert"
    if (!saved) return
    setDraft(emptyDraft())
    onDone()
  }

  const cancel = () => {
    setDraft(emptyDraft())
    clearError()
    onDone()
  }

  const setBranch = (env: CodeEnvironment, value: string) =>
    setDraft((d) => ({ ...d, branches: { ...d.branches, [env]: value } }))

  return (
    <section className="overflow-hidden rounded-xl border border-border bg-surface">
      <div className="border-b border-line px-4 py-2">
        <h3 className="text-sm font-semibold">{project ? '編輯專案' : '新增專案'}</h3>
      </div>
      <div className="space-y-4 p-4">
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
            <Label htmlFor="cp-path">專案資料夾路徑（絕對路徑）</Label>
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
                placeholder={BRANCH_PLACEHOLDER[env]}
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
            {project ? '儲存' : '新增'}
          </Button>
          {project && (
            <Button variant="ghost" onClick={cancel}>
              取消
            </Button>
          )}
        </div>
      </div>
    </section>
  )
}
