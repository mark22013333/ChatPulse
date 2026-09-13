import { useEffect, useState } from 'react'
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  Loader2Icon,
  RefreshCwIcon,
  Trash2Icon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { CodeProjectForm } from '@/components/settings/CodeProjectForm'
import { ENV_LABELS, ENV_ORDER, useCodeProjectStore } from '@/store/codeProjects'
import type { CodeProject } from '@/lib/types'

/**
 * 參考專案設定頁（ADR-0006、規格 §9.1／§9.2）。
 *
 * 在此之前這一塊塞在草稿工作區的右欄裡，1024px 以下整個消失。現在它是設定
 * 中心的一頁；工作區只留「這一次要勾哪幾個環境」的快速切換。
 *
 * 這一頁的重點是**分支環境矩陣**：拿 UAT 的程式碼回答正式環境的問題，會產生
 * 看起來有憑有據、實際上錯的答案。所以每個環境對應哪個分支要在這裡講清楚，
 * 而且驗證結果（分支還在不在）直接顯示，不要等到產草稿時才發現。
 *
 * ### 拆檔紀錄
 *
 * 這個檔在 2026-09-10 之前是一層薄殼，包著 `components/CodeProjectSettings.tsx`
 * （256 行，同時做清單與表單）。規格 §9.1 明訂那個檔名要消失、內容分到這裡
 * 與 `CodeProjectForm.tsx`。順手修掉一個薄殼帶來的缺陷：**兩層各畫一個
 * 「參考專案」的 h2**，用標題導覽的人分不出該進哪一個。
 */
export function CodeProjectsPage() {
  const projects = useCodeProjectStore((s) => s.projects)
  const loading = useCodeProjectStore((s) => s.loading)
  const error = useCodeProjectStore((s) => s.error)
  const ensureLoaded = useCodeProjectStore((s) => s.ensureLoaded)
  const verify = useCodeProjectStore((s) => s.verify)
  const remove = useCodeProjectStore((s) => s.remove)
  const clearError = useCodeProjectStore((s) => s.clearError)

  /**
   * 正在編輯哪一筆。**只存那一筆專案本身，不存表單的 draft。**
   *
   * draft 住在 `CodeProjectForm` 裡（規格 §9.3：子元件不接大 props 物件）。
   * 表單靠 `key` 在切換編輯目標時重新掛載，所以它的初始值一定跟著這一筆走，
   * 不需要在兩層之間同步 setter。
   */
  const [editing, setEditing] = useState<CodeProject | null>(null)

  useEffect(() => {
    void ensureLoaded()
  }, [ensureLoaded])

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold">參考專案</h2>
        <p className="text-fg-dim mt-1 text-xs leading-relaxed">
          登錄這台機器上的程式碼資料夾，Draft Reply 就能引用實際程式碼回答問題。
          <strong className="text-foreground">每個環境對應哪個分支要在這裡講清楚</strong>
          ——拿 UAT 的程式碼回答正式環境的問題，會產生看起來有憑有據、實際上錯的答案。
        </p>
      </div>

      {error && (
        <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}

      <section className="overflow-hidden rounded-xl border border-border bg-surface">
        <div className="border-b border-line px-4 py-2">
          <h3 className="text-sm font-semibold">已登錄的專案</h3>
        </div>
        <div className="p-4">
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
              {projects.map((project) => (
                <li key={project.id} className="rounded-lg border border-line bg-background p-4">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="font-medium">{project.name}</p>
                      <p className="truncate font-mono text-xs text-muted-foreground">
                        {project.repo_path}
                      </p>
                    </div>
                    <div className="flex shrink-0 gap-1">
                      <Button size="sm" variant="ghost" onClick={() => void verify(project.id)}>
                        <RefreshCwIcon className="size-3.5" aria-hidden />
                        重新檢查
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          clearError()
                          setEditing(project)
                        }}
                      >
                        編輯
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        // 這顆按鈕只有一個圖示，說明是它唯一的可及名稱來源
                        aria-label={`刪除 ${project.name}`}
                        onClick={() => void remove(project.id)}
                      >
                        <Trash2Icon className="size-3.5" aria-hidden />
                      </Button>
                    </div>
                  </div>

                  <BranchMatrix project={project} />

                  {project.last_verify_error && (
                    <p className="mt-2 text-xs text-caution">{project.last_verify_error}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      {/* key 讓表單在切換編輯目標時重新掛載，內部 draft 才會跟著換 */}
      <CodeProjectForm
        key={editing?.id ?? 'new'}
        project={editing}
        onDone={() => setEditing(null)}
      />
    </div>
  )
}

/**
 * 分支環境矩陣——整頁的重點。
 *
 * 只列**有填分支**的環境：沒填就是「這個環境不對應任何分支」，列出來反而讓人
 * 以為漏設了什麼。驗證結果就地顯示，分支不存在時連拼字建議一起給。
 */
function BranchMatrix({ project }: { project: CodeProject }) {
  return (
    <ul className="mt-3 space-y-1 text-sm">
      {ENV_ORDER.filter((env) => project.branches[env]).map((env) => {
        const info = project.verification?.branches?.[env]
        // 還沒驗證過就不要先畫成紅的——那會讓每個新建的專案看起來都壞掉
        const ok = info?.exists ?? true
        return (
          <li key={env} className="flex flex-wrap items-center gap-2">
            <span className="w-20 shrink-0 text-muted-foreground">{ENV_LABELS[env]}</span>
            <span aria-hidden>→</span>
            <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{project.branches[env]}</code>
            {env === project.default_env && (
              <span className="text-xs text-muted-foreground">（預設）</span>
            )}
            {info &&
              (ok ? (
                <span className="inline-flex items-center gap-1 text-xs text-verified">
                  <CheckCircle2Icon className="size-3.5" aria-hidden />
                  {info.commit}
                  {info.commit_date ? `（${info.commit_date.slice(0, 10)}）` : null}
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 text-xs text-caution">
                  <AlertTriangleIcon className="size-3.5" aria-hidden />
                  分支不存在
                  {info.did_you_mean?.length ? `，是不是 ${info.did_you_mean[0]}？` : null}
                </span>
              ))}
          </li>
        )
      })}
    </ul>
  )
}
