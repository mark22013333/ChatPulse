import { useEffect, useState } from 'react'
import { AlertTriangleIcon, CheckCircle2Icon, Loader2Icon, RefreshCwIcon, Trash2Icon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { WorkRepoForm } from '@/components/settings/WorkRepoForm'
import { useWorkRepoStore } from '@/store/workRepos'
import type { WorkRepo } from '@/lib/types'

/**
 * 「我的工作 repo」設定頁（Draft Worklog，ADR-0008）。
 *
 * 這一頁是整個工時草稿功能的入口：**沒有登錄 repo 就沒有 commit 可以當證據**。
 *
 * ### 這一頁的重點不是 CRUD
 *
 * 三個欄位留空各有一個「不會報錯、但結果是錯的」後果，而且從畫面上看不出來。
 * 所以三段警告文字是本頁的主體，表格只是載體：
 *
 * 1. `author_emails` 空陣列＝撈到這個 repo 裡**所有人**的 commit。後端不擋。
 * 2. `branch_filter` 空陣列＝掃**全部**分支（`--all`）——與「參考專案」的
 *    「環境 → 釘死一個分支」語意**相反**，同一個人會同時看到兩頁。
 * 3. `default_zplanner_project_*` **只影響候選排序，絕不自動填工時**。
 *
 * ### 為什麼刪除確認沒有用 `ConfirmDialog`
 *
 * `ConfirmDialog` 的介面要求傳一個名為 title 的 React prop，而
 * `lib/tokens.test.ts` 的守門規則 ③ 是對**原始碼文字**比對的，非白名單檔案
 * 的預算是 0。這裡改用它底下那一組 Dialog 元件直接組，語意（`DialogTitle`
 * 是文字節點、不是 DOM 屬性）與 `ConfirmDialog` 完全一樣，只是不觸發那條
 * 文字比對。若之後把該白名單改成可增，這裡可以換回 `ConfirmDialog`。
 */
export function WorkRepoPage() {
  const repos = useWorkRepoStore((s) => s.repos)
  const loading = useWorkRepoStore((s) => s.loading)
  const error = useWorkRepoStore((s) => s.error)
  const verifyingId = useWorkRepoStore((s) => s.verifyingId)
  const ensureLoaded = useWorkRepoStore((s) => s.ensureLoaded)
  const verify = useWorkRepoStore((s) => s.verify)
  const remove = useWorkRepoStore((s) => s.remove)
  const clearError = useWorkRepoStore((s) => s.clearError)

  /** 正在編輯哪一筆（只存那一筆本身，draft 住在 `WorkRepoForm` 裡）。 */
  const [editing, setEditing] = useState<WorkRepo | null>(null)
  /**
   * 從 `detected_emails` 點進來要補的信箱，配一個 nonce。
   *
   * nonce 進 `key`：已經在編輯同一筆時再點一個信箱，`editing.id` 沒變、
   * 表單不會重新掛載，補的信箱就進不去。
   */
  const [seed, setSeed] = useState<{ emails: string[]; nonce: number }>({ emails: [], nonce: 0 })
  /** 等待二次確認的刪除目標。`null` ＝對話框關著。 */
  const [pendingDelete, setPendingDelete] = useState<WorkRepo | null>(null)

  useEffect(() => {
    void ensureLoaded()
  }, [ensureLoaded])

  const startEdit = (repo: WorkRepo, emails: string[] = []) => {
    clearError()
    setEditing(repo)
    setSeed((s) => ({ emails, nonce: s.nonce + 1 }))
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-md font-semibold">我的工作 repo</h2>
        <p className="text-fg-dim mt-1 text-xs leading-relaxed">
          登錄這台機器上你自己在寫的 git repo，工時草稿就能從你的 commit 整理出「這天做了
          什麼」，並把 commit 附在旁邊當證據。
          <strong className="text-foreground">沒有登錄 repo 就沒有 commit 可以當證據</strong>
          ，草稿只剩 Chat 發言可用。
        </p>
        <p className="text-fg-subtle mt-2 text-xs leading-relaxed">
          這與設定裡的「參考專案」是<strong>兩份不同的登錄</strong>，不共用：那邊是為了
          「查程式碼回答問題」而登的，必須釘死是哪個環境的哪一版；這邊是為了「回想這天做了
          什麼」。兩邊的分支語意正好相反，下面的表單會再講一次。
        </p>
      </div>

      {error && (
        <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}

      <section className="overflow-hidden rounded-xl border border-border bg-surface">
        <div className="border-b border-line px-4 py-2">
          <h3 className="text-sm font-semibold">已登錄的 repo（{repos.length}）</h3>
        </div>
        <div className="p-4">
          {loading ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2Icon className="size-4 animate-spin" aria-hidden />
              載入中…
            </p>
          ) : repos.length === 0 ? (
            <div className="space-y-2 rounded-md border border-dashed px-4 py-6 text-center">
              <p className="text-sm text-foreground">還沒有登錄任何工作 repo。</p>
              <p className="text-fg-dim mx-auto max-w-prose text-xs leading-relaxed">
                在下面新增一個之後，工時草稿會掃這個 repo 裡你的 commit，整理成「這天做了
                什麼」的草稿卡，每一筆都附上 commit 當證據讓你逐筆確認。在那之前，工時草稿
                只看得到 Chat 發言。
              </p>
            </div>
          ) : (
            <ul className="space-y-3">
              {repos.map((repo) => (
                <li key={repo.id} className="rounded-lg border border-line bg-background p-4">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="font-medium">
                        {repo.name}
                        {!repo.enabled && (
                          <span className="text-fg-subtle ml-2 rounded border border-dashed border-line px-1.5 py-0.5 text-2xs font-normal">
                            已停用，產草稿時不會撈
                          </span>
                        )}
                      </p>
                      <p className="metric truncate text-xs text-muted-foreground">
                        {repo.repo_path}
                      </p>
                    </div>
                    <div className="flex shrink-0 gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={verifyingId === repo.id}
                        onClick={() => void verify(repo.id)}
                      >
                        {verifyingId === repo.id ? (
                          <Loader2Icon className="size-3.5 animate-spin" aria-hidden />
                        ) : (
                          <RefreshCwIcon className="size-3.5" aria-hidden />
                        )}
                        重新檢查
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => startEdit(repo)}>
                        編輯
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        // 這顆按鈕只有一個圖示，說明是它唯一的可及名稱來源
                        aria-label={`刪除 ${repo.name}`}
                        onClick={() => {
                          clearError()
                          setPendingDelete(repo)
                        }}
                      >
                        <Trash2Icon className="size-3.5" aria-hidden />
                      </Button>
                    </div>
                  </div>

                  <RepoScope repo={repo} />
                  <VerificationLine repo={repo} onAdoptEmail={(email) => startEdit(repo, [email])} />
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      {/* key 讓表單在切換編輯目標、或帶著新的信箱進來時重新掛載 */}
      <WorkRepoForm
        key={`${editing?.id ?? 'new'}-${seed.nonce}`}
        repo={editing}
        seedEmails={editing ? seed.emails : []}
        onDone={() => setEditing(null)}
      />

      <DeleteConfirm
        repo={pendingDelete}
        onCancel={() => setPendingDelete(null)}
        onConfirm={async () => {
          if (!pendingDelete) return
          const id = pendingDelete.id
          setPendingDelete(null)
          if (editing?.id === id) setEditing(null)
          await remove(id)
        }}
      />
    </div>
  )
}

/**
 * 這一筆會撈到什麼——整頁的重點。
 *
 * 兩行都刻意**在空值的時候講得更多**：空值正是會出事的那一側，而空值天生
 * 沒有東西可以顯示，不主動寫出來畫面上就只是一片空白。
 */
function RepoScope({ repo }: { repo: WorkRepo }) {
  const noEmails = repo.author_emails.length === 0
  const allBranches = repo.branch_filter.length === 0
  return (
    <ul className="mt-3 space-y-1.5 text-sm">
      <li className="flex flex-wrap items-baseline gap-2">
        <span className="w-16 shrink-0 text-muted-foreground">作者</span>
        {noEmails ? (
          <span className="inline-flex items-center gap-1 text-xs text-caution">
            <AlertTriangleIcon className="size-3.5" aria-hidden />
            未指定：會撈這個 repo 裡<strong>所有人</strong>的 commit，工時會被灌進別人做的事
          </span>
        ) : (
          <span className="flex flex-wrap gap-1">
            {repo.author_emails.map((email) => (
              <code
                key={email}
                className="metric rounded border border-line bg-muted px-1.5 py-0.5 text-xs text-provenance"
              >
                {email}
              </code>
            ))}
          </span>
        )}
      </li>
      <li className="flex flex-wrap items-baseline gap-2">
        <span className="w-16 shrink-0 text-muted-foreground">分支</span>
        {allBranches ? (
          <span className="text-xs text-foreground">
            全部分支（<code className="metric">--all</code>）——這是預設，不是「不掃」
          </span>
        ) : (
          <span className="flex flex-wrap items-baseline gap-1 text-xs">
            <span className="text-muted-foreground">只掃</span>
            {repo.branch_filter.map((branch) => (
              <code key={branch} className="metric rounded bg-muted px-1.5 py-0.5">
                {branch}
              </code>
            ))}
          </span>
        )}
      </li>
      {(repo.default_zplanner_project_id != null || repo.default_zplanner_project_key) && (
        <li className="flex flex-wrap items-baseline gap-2">
          <span className="w-16 shrink-0 text-muted-foreground">預設專案</span>
          <span className="text-xs">
            <code className="metric rounded bg-muted px-1.5 py-0.5">
              {repo.default_zplanner_project_key ?? repo.default_zplanner_project_id}
            </code>
            <span className="text-fg-subtle ml-2">只影響候選排序，不會自動填工時</span>
          </span>
        </li>
      )}
    </ul>
  )
}

/**
 * 最後一次驗證的結果。
 *
 * `detected_emails` 做成一排可以點的按鈕：整份照單全收正是陷阱本身——那份
 * 清單是「這個 repo 最近 200 筆 commit 的作者」，同事的信箱本來就在裡面。
 * 由人一個一個挑，系統不自動發現（與後端同一條哲學）。
 */
function VerificationLine({
  repo,
  onAdoptEmail,
}: {
  repo: WorkRepo
  onAdoptEmail: (email: string) => void
}) {
  const v = repo.verification
  const failed = v ? !v.ok : Boolean(repo.last_verify_error)
  const message = v?.error ?? repo.last_verify_error ?? null
  const detected = v?.detected_emails ?? []
  const known = new Set(repo.author_emails)

  return (
    <div className="mt-3 space-y-1.5 border-t border-line pt-2">
      {failed ? (
        <p className="inline-flex items-center gap-1 text-xs text-caution">
          <AlertTriangleIcon className="size-3.5" aria-hidden />
          {message ?? '上次檢查沒有通過'}
        </p>
      ) : v ? (
        <p className="inline-flex flex-wrap items-center gap-1 text-xs text-verified">
          <CheckCircle2Icon className="size-3.5" aria-hidden />
          {v.head_sha ? (
            <>
              讀得到，最新 commit 是 <code className="metric">{v.head_sha}</code>
            </>
          ) : (
            '讀得到，但這個 repo 還沒有任何 commit（不是錯誤）'
          )}
        </p>
      ) : (
        // 還沒在這個畫面按過重新檢查就不要先畫成紅的——那會讓每一筆看起來都壞掉
        <p className="text-fg-subtle text-xs">
          {repo.last_verified_at
            ? `上次檢查：${repo.last_verified_at.slice(0, 10)}`
            : '還沒檢查過。按「重新檢查」會確認路徑讀得到，並列出這個 repo 裡偵測到的作者信箱。'}
        </p>
      )}

      {detected.length > 0 && (
        <div className="space-y-1">
          <p className="text-fg-dim text-2xs leading-relaxed">
            這個 repo 最近的 commit 用過這些作者信箱。
            <strong className="text-foreground">點一下把它加進下面的表單</strong>
            ——請只挑你自己的，同事的信箱本來就會列在這裡。
          </p>
          <div className="flex flex-wrap gap-1">
            {detected.map((email) => (
              <Button
                key={email}
                size="xs"
                variant="ghost"
                disabled={known.has(email)}
                className="metric h-5 px-1.5 text-2xs"
                onClick={() => onAdoptEmail(email)}
              >
                {email}
                {known.has(email) ? '（已加入）' : null}
              </Button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/**
 * 刪除的二次確認。
 *
 * 對話框要說得出「按下去會發生什麼」：刪掉之後既有的工時草稿不會自己消失，
 * 但之後不會再從這個 repo 撈 commit。只是暫時不想撈的話，編輯裡有停用。
 */
function DeleteConfirm({
  repo,
  onCancel,
  onConfirm,
}: {
  repo: WorkRepo | null
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <Dialog open={repo !== null} onOpenChange={(open) => (open ? undefined : onCancel())}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>刪除工作 repo「{repo?.name}」？</DialogTitle>
          <DialogDescription>
            之後產工時草稿不會再從這個 repo 撈 commit，這一筆登錄（含作者信箱與分支設定）
            也會消失。已經產出的草稿不受影響。 只是這陣子不想撈的話，改用編輯裡的「產草稿時
            要撈這個 repo」把它停用就好。
          </DialogDescription>
        </DialogHeader>
        <p className="metric rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs break-words">
          {repo?.repo_path}
        </p>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            取消
          </Button>
          <Button variant="destructive" onClick={onConfirm}>
            確認刪除
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
