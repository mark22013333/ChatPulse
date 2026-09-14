import { useState } from 'react'
import { Loader2Icon, PlusIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  draftFromWorkRepo,
  emptyWorkRepoDraft,
  useWorkRepoStore,
  type WorkRepoDraft,
} from '@/store/workRepos'
import type { WorkRepo } from '@/lib/types'

interface WorkRepoFormProps {
  /** 要編輯的那一筆；`null` 是新增。 */
  repo: WorkRepo | null
  /**
   * 從清單的 `detected_emails` 點進來要補上的信箱。
   *
   * 只在掛載時吃一次——呼叫端用 `key` 讓這個元件重新掛載，所以每點一次
   * 都會帶著新的 seed 重新初始化，不需要在兩層之間同步 setter。
   */
  seedEmails?: string[]
  /** 送出成功或按取消之後通知呼叫端（讓它把編輯目標清掉）。 */
  onDone: () => void
}

/**
 * 「我的工作 repo」的新增／編輯表單（ADR-0008）。
 *
 * **draft 住在這裡，不由 `WorkRepoPage` 傳進來**（比照 `CodeProjectForm`：
 * 子元件不接大 props 物件）。切換編輯目標時由呼叫端用 `key` 讓這個元件
 * 重新掛載，初始值就跟著 `repo` 走。
 *
 * 這一頁真正要傳達的不是「有哪些欄位」，是**三個留空會出事的欄位各自代表
 * 什麼**。三段說明文字不是裝飾，它們是這個表單存在的理由：
 *
 * 1. 作者信箱留空＝撈到這個 repo 裡**所有人**的 commit。
 * 2. 分支留空＝掃**全部**分支（與「參考專案」的語意相反）。
 * 3. 預設 ZPlanner 專案**只影響候選排序，絕不自動填工時**。
 */
export function WorkRepoForm({ repo, seedEmails = [], onDone }: WorkRepoFormProps) {
  const saving = useWorkRepoStore((s) => s.saving)
  const create = useWorkRepoStore((s) => s.create)
  const update = useWorkRepoStore((s) => s.update)
  const clearError = useWorkRepoStore((s) => s.clearError)

  const [draft, setDraft] = useState<WorkRepoDraft>(() =>
    repo ? draftFromWorkRepo(repo, seedEmails) : emptyWorkRepoDraft(),
  )

  const submit = async () => {
    const saved = repo ? await update(repo.id, draft) : await create(draft)
    // 失敗時**不要**清表單：使用者剛打的東西還在上面，清掉等於叫他重打一次。
    // 錯誤訊息由 store 的 error 承擔，畫在清單頁上方的 role="alert"
    if (!saved) return
    setDraft(emptyWorkRepoDraft())
    onDone()
  }

  const cancel = () => {
    setDraft(emptyWorkRepoDraft())
    clearError()
    onDone()
  }

  return (
    <section className="overflow-hidden rounded-xl border border-border bg-surface">
      <div className="border-b border-line px-4 py-2">
        <h3 className="text-sm font-semibold">{repo ? '編輯工作 repo' : '新增工作 repo'}</h3>
      </div>
      <div className="space-y-4 p-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="wr-name">名稱</Label>
            <Input
              id="wr-name"
              value={draft.name}
              placeholder="智慧客服後端"
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="wr-path">repo 路徑（絕對路徑）</Label>
            <Input
              id="wr-path"
              value={draft.repo_path}
              placeholder="/Users/me/repos/cs-backend"
              onChange={(e) => setDraft((d) => ({ ...d, repo_path: e.target.value }))}
            />
            <p className="text-fg-subtle text-2xs leading-relaxed">
              相對路徑會相對於伺服器的工作目錄去解，那跟你心裡想的不是同一個地方。
            </p>
          </div>
        </div>

        {/* ── 陷阱 1：留空＝撈到所有人的 commit ───────────────────── */}
        <div className="space-y-1.5">
          <Label htmlFor="wr-emails">作者信箱（一行一個，或用逗號分隔）</Label>
          <Textarea
            id="wr-emails"
            rows={3}
            value={draft.author_emails}
            placeholder={'me@corp.com\nme@personal.dev'}
            onChange={(e) => setDraft((d) => ({ ...d, author_emails: e.target.value }))}
          />
          <p className="border-caution-line text-2xs border-l-2 py-0.5 pl-2 leading-relaxed text-caution">
            ⚠️ 留空＝<strong>不限作者</strong>：這個 repo
            裡所有人的 commit 都會被算成你的工時，草稿會被灌進別人做的事。
            系統不會擋，也不會提醒第二次。
          </p>
          <p className="text-fg-subtle text-2xs leading-relaxed">
            要填<strong>多個</strong>：同一個人在不同 repo 常常設不同的
            <code className="metric mx-1">user.email</code>
            （公司信箱、個人信箱、GitHub 的 noreply），只填一個會讓另一個信箱的 commit
            靜默漏掉——草稿照樣產得出來，只是少了那幾筆工時。 不確定自己在這個 repo
            用哪個信箱，就先存檔、再按清單上的「重新檢查」，偵測到的信箱會列出來讓你點。
          </p>
        </div>

        {/* ── 陷阱 2：分支語意與「參考專案」相反 ─────────────────── */}
        <div className="space-y-1.5">
          <Label htmlFor="wr-branches">分支白名單（一行一個，或用逗號分隔）</Label>
          <Textarea
            id="wr-branches"
            rows={2}
            value={draft.branch_filter}
            placeholder="留空就是掃全部分支"
            onChange={(e) => setDraft((d) => ({ ...d, branch_filter: e.target.value }))}
          />
          <p className="text-2xs leading-relaxed text-foreground">
            <strong>留空＝掃全部分支</strong>（<code className="metric">--all</code>
            ），不是「不掃」。填了才當白名單。
          </p>
          <p className="text-fg-subtle text-2xs leading-relaxed">
            這與設定裡的「參考專案」<strong>正好相反</strong>——那邊是「環境 →
            釘死一個分支」，拿錯環境等於給出錯的答案；這邊漏掉一個還沒合併的 feature
            分支，就等於漏掉那天的工作。同一個人會同時用到兩頁，別把兩邊的習慣搬過來。
          </p>
        </div>

        {/* ── 陷阱 3：預設專案只排序，不自動填 ───────────────────── */}
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">預設 ZPlanner 專案（選填）</legend>
          <p className="text-2xs leading-relaxed text-foreground">
            <strong>只用來把這個專案的 issue 排進候選清單最前面並標示來源，絕不自動填工時。</strong>
            專案歸屬一律由你在確認畫面自己選——工時會進月結，月結後不能直接改、要走
            correction-request，而猜錯的那次長得跟猜對的一模一樣。
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="wr-zp-id" className="font-normal">
                專案 ID（數字）
              </Label>
              <Input
                id="wr-zp-id"
                inputMode="numeric"
                value={draft.default_zplanner_project_id}
                placeholder="42"
                onChange={(e) =>
                  setDraft((d) => ({ ...d, default_zplanner_project_id: e.target.value }))
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="wr-zp-key" className="font-normal">
                專案代號
              </Label>
              <Input
                id="wr-zp-key"
                value={draft.default_zplanner_project_key}
                placeholder="PEI"
                onChange={(e) =>
                  setDraft((d) => ({ ...d, default_zplanner_project_key: e.target.value }))
                }
              />
            </div>
          </div>
          <p className="text-fg-subtle text-2xs leading-relaxed">
            兩個都留空＝這個 repo 沒有預設專案。清空後儲存會真的清掉，不是「不改」。
          </p>
        </fieldset>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => setDraft((d) => ({ ...d, enabled: e.target.checked }))}
          />
          產草稿時要撈這個 repo
        </label>

        <div className="flex gap-2">
          <Button onClick={() => void submit()} disabled={saving}>
            {saving ? (
              <Loader2Icon className="size-4 animate-spin" aria-hidden />
            ) : (
              <PlusIcon className="size-4" aria-hidden />
            )}
            {repo ? '儲存' : '新增'}
          </Button>
          {repo && (
            <Button variant="ghost" onClick={cancel}>
              取消
            </Button>
          )}
        </div>
      </div>
    </section>
  )
}
