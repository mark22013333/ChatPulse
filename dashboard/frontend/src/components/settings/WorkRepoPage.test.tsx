import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkRepoPage } from './WorkRepoPage'
import { useWorkRepoStore } from '@/store/workRepos'
import type { WorkRepo } from '@/lib/types'

/**
 * 「我的工作 repo」設定頁（Draft Worklog，ADR-0008）。
 *
 * 斷言集中在**三個會靜默出錯的欄位有沒有被講出來**，而不是版面：
 *
 * 1. `author_emails` 空陣列＝撈到這個 repo 裡所有人的 commit。
 * 2. `branch_filter` 空陣列＝掃全部分支（與「參考專案」相反）。
 * 3. 預設 ZPlanner 專案只影響候選排序，絕不自動填工時。
 *
 * 這三條若被「順手簡化文案」拿掉，畫面看起來一樣正常，而工時會算錯——
 * 所以它們是測試，不是註解。
 */

function workRepo(over: Partial<WorkRepo> = {}): WorkRepo {
  return {
    id: 1,
    name: '智慧客服後端',
    repo_path: '/Users/cheng/repos/cs-backend',
    author_emails: ['me@corp.com'],
    branch_filter: [],
    default_zplanner_project_id: null,
    default_zplanner_project_key: null,
    enabled: true,
    ...over,
  }
}

/** 只 seed 資料，不讓 ensureLoaded 真的打 API。 */
function seed(repos: WorkRepo[], over: Record<string, unknown> = {}) {
  useWorkRepoStore.setState({
    repos,
    loading: false,
    saving: false,
    error: null,
    loaded: true,
    verifyingId: null,
    ensureLoaded: async () => {},
    ...over,
  })
}

beforeEach(() => {
  seed([])
})

describe('我的工作 repo：清單', () => {
  it('**空狀態說得出「登錄之後會怎樣」**，不是只寫「尚無資料」', () => {
    render(<WorkRepoPage />)
    expect(screen.getByText(/還沒有登錄任何工作 repo/)).toBeInTheDocument()
    // 空狀態要交代後果：沒有 repo 就沒有 commit 可以當證據
    expect(screen.getByText(/整理成「這天做了\s*什麼」的草稿卡/)).toBeInTheDocument()
    expect(screen.getByText(/只看得到 Chat 發言/)).toBeInTheDocument()
  })

  it('載入中顯示載入狀態', () => {
    seed([], { loading: true })
    render(<WorkRepoPage />)
    expect(screen.getByText('載入中…')).toBeInTheDocument()
  })

  it('列出名稱、路徑與作者信箱', () => {
    seed([workRepo({ author_emails: ['me@corp.com', 'me@personal.dev'] })])
    render(<WorkRepoPage />)
    expect(screen.getByText('智慧客服後端')).toBeInTheDocument()
    expect(screen.getByText('/Users/cheng/repos/cs-backend')).toBeInTheDocument()
    const row = screen.getByText('智慧客服後端').closest('li') as HTMLElement
    expect(within(row).getByText('me@corp.com')).toBeInTheDocument()
    expect(within(row).getByText('me@personal.dev')).toBeInTheDocument()
  })

  it('停用的那一筆說得出「產草稿時不會撈」', () => {
    seed([workRepo({ enabled: false })])
    render(<WorkRepoPage />)
    expect(screen.getByText(/已停用，產草稿時不會撈/)).toBeInTheDocument()
  })

  it('**陷阱 1：作者信箱空著時，清單上直接寫出「會撈所有人的 commit」**', () => {
    seed([workRepo({ author_emails: [] })])
    render(<WorkRepoPage />)
    const row = screen.getByText('智慧客服後端').closest('li') as HTMLElement
    expect(within(row).getByText(/會撈這個 repo 裡/)).toHaveTextContent(
      /所有人.*的 commit，工時會被灌進別人做的事/,
    )
  })

  it('**陷阱 2：分支空著時寫明「全部分支（--all），這是預設不是不掃」**', () => {
    seed([workRepo({ branch_filter: [] })])
    render(<WorkRepoPage />)
    const row = screen.getByText('智慧客服後端').closest('li') as HTMLElement
    expect(within(row).getByText(/全部分支/)).toHaveTextContent(/這是預設，不是「不掃」/)
  })

  it('分支有值時列成白名單，不再顯示「全部分支」', () => {
    seed([workRepo({ branch_filter: ['main', 'feature/x'] })])
    render(<WorkRepoPage />)
    const row = screen.getByText('智慧客服後端').closest('li') as HTMLElement
    expect(within(row).getByText('只掃')).toBeInTheDocument()
    expect(within(row).getByText('main')).toBeInTheDocument()
    expect(within(row).getByText('feature/x')).toBeInTheDocument()
    expect(within(row).queryByText(/全部分支/)).toBeNull()
  })

  it('**陷阱 3：有預設專案時，旁邊寫明「只影響候選排序，不會自動填工時」**', () => {
    seed([workRepo({ default_zplanner_project_id: 42, default_zplanner_project_key: 'PEI' })])
    render(<WorkRepoPage />)
    const row = screen.getByText('智慧客服後端').closest('li') as HTMLElement
    expect(within(row).getByText('PEI')).toBeInTheDocument()
    expect(within(row).getByText(/只影響候選排序，不會自動填工時/)).toBeInTheDocument()
  })

  it('**這一頁自己講清楚它與「參考專案」是兩份不同的登錄**', () => {
    render(<WorkRepoPage />)
    expect(screen.getByText(/兩份不同的登錄/)).toBeInTheDocument()
  })

  it('store 的錯誤走 role="alert"', () => {
    seed([], { error: '這個路徑不是 git repo' })
    render(<WorkRepoPage />)
    expect(screen.getByRole('alert')).toHaveTextContent('這個路徑不是 git repo')
  })

  it('上次驗證失敗的訊息直接顯示在那一列', () => {
    seed([workRepo({ last_verify_error: '路徑不存在或不是資料夾' })])
    render(<WorkRepoPage />)
    expect(screen.getByText('路徑不存在或不是資料夾')).toBeInTheDocument()
  })
})

describe('我的工作 repo：重新檢查', () => {
  it('按重新檢查會帶著 id 呼叫 verify', async () => {
    const verify = vi.fn(async () => {})
    seed([workRepo()], { verify })
    render(<WorkRepoPage />)

    const row = screen.getByText('智慧客服後端').closest('li') as HTMLElement
    await userEvent.click(within(row).getByRole('button', { name: /重新檢查/ }))
    expect(verify).toHaveBeenCalledWith(1)
  })

  it('**驗證成功後把 head_sha 顯示出來**', () => {
    seed([
      workRepo({
        verification: { ok: true, error: null, head_sha: 'a3f91c2', detected_emails: [] },
      }),
    ])
    render(<WorkRepoPage />)
    expect(screen.getByText('a3f91c2')).toBeInTheDocument()
  })

  it('剛 git init 還沒有 commit 時說明那不是錯誤', () => {
    seed([
      workRepo({
        verification: { ok: true, error: null, head_sha: null, detected_emails: [] },
      }),
    ])
    render(<WorkRepoPage />)
    expect(screen.getByText(/還沒有任何 commit（不是錯誤）/)).toBeInTheDocument()
  })

  it('**驗證後把 detected_emails 列出來，並提醒同事的信箱也在裡面**', () => {
    seed([
      workRepo({
        author_emails: [],
        verification: {
          ok: true,
          error: null,
          head_sha: 'a3f91c2',
          detected_emails: ['me@corp.com', 'someone-else@corp.com'],
        },
      }),
    ])
    render(<WorkRepoPage />)

    expect(screen.getByRole('button', { name: 'me@corp.com' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'someone-else@corp.com' })).toBeInTheDocument()
    // 一鍵全收正是陷阱本身，文字要說出「只挑你自己的」
    expect(screen.getByText(/請只挑你自己的，同事的信箱本來就會列在這裡/)).toBeInTheDocument()
  })

  it('**點偵測到的信箱會把它填進編輯表單**（一鍵填入的引導）', async () => {
    seed([
      workRepo({
        author_emails: [],
        verification: {
          ok: true,
          error: null,
          head_sha: 'a3f91c2',
          detected_emails: ['me@corp.com'],
        },
      }),
    ])
    render(<WorkRepoPage />)

    await userEvent.click(screen.getByRole('button', { name: 'me@corp.com' }))

    expect(screen.getByRole('heading', { name: '編輯工作 repo' })).toBeInTheDocument()
    expect(screen.getByLabelText(/作者信箱/)).toHaveValue('me@corp.com')
  })

  it('已經在 author_emails 裡的信箱標成「已加入」並鎖住', () => {
    seed([
      workRepo({
        author_emails: ['me@corp.com'],
        verification: {
          ok: true,
          error: null,
          head_sha: 'a3f91c2',
          detected_emails: ['me@corp.com'],
        },
      }),
    ])
    render(<WorkRepoPage />)
    expect(screen.getByRole('button', { name: /me@corp.com（已加入）/ })).toBeDisabled()
  })
})

describe('我的工作 repo：新增／編輯表單', () => {
  it('預設是「新增工作 repo」，填完送出會帶著 draft 呼叫 create', async () => {
    const create = vi.fn(async () => workRepo())
    seed([], { create })
    render(<WorkRepoPage />)

    expect(screen.getByRole('heading', { name: '新增工作 repo' })).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('名稱'), '訂閱系統')
    await userEvent.type(screen.getByLabelText(/repo 路徑/), '/srv/sub')
    await userEvent.type(screen.getByLabelText(/作者信箱/), 'me@corp.com')

    await userEvent.click(screen.getByRole('button', { name: '新增' }))
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({
        name: '訂閱系統',
        repo_path: '/srv/sub',
        author_emails: 'me@corp.com',
        branch_filter: '',
        enabled: true,
      }),
    )
  })

  it('**三個陷阱在表單上各有一段可見文字**', () => {
    render(<WorkRepoPage />)
    // 1. 作者信箱留空＝不限作者
    expect(screen.getByText(/這個 repo\s*裡所有人的 commit 都會被算成你的工時/)).toBeInTheDocument()
    // 2. 分支留空＝掃全部分支，且與「參考專案」相反
    expect(screen.getByText(/留空＝掃全部分支/)).toBeInTheDocument()
    // 「正好相反」在頁首導言也出現過一次，所以比對表單裡獨有的那半句
    expect(screen.getByText(/拿錯環境等於給出錯的答案/)).toBeInTheDocument()
    // 3. 預設專案只排序不自動填
    expect(
      screen.getByText(/只用來把這個專案的 issue 排進候選清單最前面並標示來源，絕不自動填工時。/),
    ).toBeInTheDocument()
  })

  it('**按編輯把那一筆填進表單**，送出走 update 而不是 create', async () => {
    const update = vi.fn(async () => workRepo())
    const create = vi.fn(async () => workRepo())
    seed([workRepo({ branch_filter: ['main'] })], { update, create })
    render(<WorkRepoPage />)

    await userEvent.click(screen.getByRole('button', { name: '編輯' }))

    expect(screen.getByRole('heading', { name: '編輯工作 repo' })).toBeInTheDocument()
    expect(screen.getByLabelText('名稱')).toHaveValue('智慧客服後端')
    expect(screen.getByLabelText(/repo 路徑/)).toHaveValue('/Users/cheng/repos/cs-backend')
    expect(screen.getByLabelText(/作者信箱/)).toHaveValue('me@corp.com')
    expect(screen.getByLabelText(/分支白名單/)).toHaveValue('main')

    await userEvent.click(screen.getByRole('button', { name: '儲存' }))
    expect(update).toHaveBeenCalledWith(1, expect.objectContaining({ name: '智慧客服後端' }))
    expect(create).not.toHaveBeenCalled()
  })

  it('編輯中按取消回到「新增工作 repo」的空表單', async () => {
    seed([workRepo()])
    render(<WorkRepoPage />)

    await userEvent.click(screen.getByRole('button', { name: '編輯' }))
    await userEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(screen.getByRole('heading', { name: '新增工作 repo' })).toBeInTheDocument()
    expect(screen.getByLabelText('名稱')).toHaveValue('')
    expect(screen.queryByRole('button', { name: '取消' })).toBeNull()
  })

  it('儲存中把送出鈕鎖住', () => {
    seed([], { saving: true })
    render(<WorkRepoPage />)
    expect(screen.getByRole('button', { name: '新增' })).toBeDisabled()
  })
})

describe('我的工作 repo：刪除要二次確認', () => {
  it('**按刪除不會直接刪，先跳確認對話框**', async () => {
    const remove = vi.fn(async () => true)
    seed([workRepo()], { remove })
    render(<WorkRepoPage />)

    await userEvent.click(screen.getByRole('button', { name: '刪除 智慧客服後端' }))
    expect(remove).not.toHaveBeenCalled()

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/刪除工作 repo「智慧客服後端」？/)).toBeInTheDocument()
    // 對話框要說得出按下去會發生什麼，以及有「停用」這條比較輕的路
    expect(within(dialog).getByText(/不會再從這個 repo 撈 commit/)).toBeInTheDocument()
    expect(within(dialog).getByText(/把它停用就好/)).toBeInTheDocument()
    expect(within(dialog).getByText('/Users/cheng/repos/cs-backend')).toBeInTheDocument()
  })

  it('在對話框按取消不會刪掉', async () => {
    const remove = vi.fn(async () => true)
    seed([workRepo()], { remove })
    render(<WorkRepoPage />)

    await userEvent.click(screen.getByRole('button', { name: '刪除 智慧客服後端' }))
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '取消' }))

    expect(remove).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('按「確認刪除」才真的呼叫 remove', async () => {
    const remove = vi.fn(async () => true)
    seed([workRepo()], { remove })
    render(<WorkRepoPage />)

    await userEvent.click(screen.getByRole('button', { name: '刪除 智慧客服後端' }))
    await userEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: '確認刪除' }),
    )

    expect(remove).toHaveBeenCalledWith(1)
  })
})

describe('我的工作 repo：設計紅線', () => {
  it('**渲染結果裡 [title] 選得到 0 個**（設計規格 §10.3）', () => {
    seed([
      workRepo({
        last_verify_error: '路徑不存在或不是資料夾',
        verification: {
          ok: true,
          error: null,
          head_sha: 'a3f91c2',
          detected_emails: ['me@corp.com'],
        },
      }),
    ])
    const { container } = render(<WorkRepoPage />)
    expect(container.querySelectorAll('[title]')).toHaveLength(0)
  })

  it('**只有一個「我的工作 repo」標題**', () => {
    render(<WorkRepoPage />)
    expect(screen.getAllByRole('heading', { name: '我的工作 repo' })).toHaveLength(1)
  })
})
