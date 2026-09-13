import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CodeProjectsPage } from './CodeProjectsPage'
import { useCodeProjectStore } from '@/store/codeProjects'
import type { CodeProject } from '@/lib/types'

/**
 * 參考專案設定頁（規格 §9.1 的拆檔）。
 *
 * **這一份是特徵測試（characterisation test）**：先寫、先拿它跑**重構前**的
 * 程式碼確認會過，再跑重構後。只跑重構後的版本證明不了任何事——那只證明
 * 新程式碼自己一致。這個做法在 commit 14579bf（抽共用下拉）用過一次，
 * 是「外部行為沒變」唯一站得住的證據。
 *
 * 這一頁的重點是**分支環境矩陣**：拿 UAT 的程式碼回答正式環境的問題，會
 * 產生看起來有憑有據、實際上錯的答案。所以斷言集中在「環境 → 分支 → 驗證
 * 結果」讀得出來，而不是版面。
 */

function project(over: Partial<CodeProject> = {}): CodeProject {
  return {
    id: 1,
    name: '智慧客服後端',
    repo_path: '/Users/cheng/IdeaProjects/cs-backend',
    default_env: 'production',
    include_globs: [],
    exclude_globs: [],
    enabled: true,
    branches: { production: 'main', uat: 'release/uat' },
    ...over,
  }
}

/** 只 seed 資料，不讓 ensureLoaded 真的打 API。 */
function seed(projects: CodeProject[], over: Record<string, unknown> = {}) {
  useCodeProjectStore.setState({
    projects,
    loading: false,
    saving: false,
    error: null,
    loaded: true,
    ensureLoaded: async () => {},
    ...over,
  })
}

beforeEach(() => {
  seed([])
})

describe('參考專案：清單與分支矩陣', () => {
  it('沒有專案時說得出「還沒有登錄任何專案」', () => {
    render(<CodeProjectsPage />)
    expect(screen.getByText(/還沒有登錄任何專案/)).toBeInTheDocument()
  })

  it('載入中顯示載入狀態', () => {
    seed([], { loading: true })
    render(<CodeProjectsPage />)
    expect(screen.getByText('載入中…')).toBeInTheDocument()
  })

  it('列出專案名稱與路徑', () => {
    seed([project()])
    render(<CodeProjectsPage />)
    expect(screen.getByText('智慧客服後端')).toBeInTheDocument()
    expect(screen.getByText('/Users/cheng/IdeaProjects/cs-backend')).toBeInTheDocument()
  })

  it('**分支矩陣逐環境列出對應的分支，並標出預設**', () => {
    seed([project()])
    render(<CodeProjectsPage />)

    // 斷言一定要限縮在那一列：環境名同時是下方表單的 Label，
    // 不限縮會撞上 getByText 的「找到多個」
    const row = screen.getByText('智慧客服後端').closest('li') as HTMLElement
    expect(within(row).getByText('正式環境')).toBeInTheDocument()
    expect(within(row).getByText('main')).toBeInTheDocument()
    expect(within(row).getByText('UAT 環境')).toBeInTheDocument()
    expect(within(row).getByText('release/uat')).toBeInTheDocument()
    // 沒填分支的環境不出現在矩陣裡（「這個環境不對應任何分支」）
    expect(within(row).queryByText('開發環境')).toBeNull()
    expect(within(row).getByText('（預設）')).toBeInTheDocument()
  })

  it('**分支驗證成功時直接顯示 commit**，不用等到產草稿才發現', () => {
    seed([
      project({
        verification: {
          repo_ok: true,
          working_tree_dirty: false,
          branches: {
            production: {
              branch: 'main',
              exists: true,
              commit: 'a1b2c3d',
              commit_date: '2026-09-08T10:00:00Z',
            },
          },
        },
      }),
    ])
    render(<CodeProjectsPage />)

    // commit 與日期在同一個 span 裡（兩個相鄰的文字節點），所以用整段比對
    expect(screen.getByText(/a1b2c3d（2026-09-08）/)).toBeInTheDocument()
  })

  it('**分支不存在時說出來，還給拼字建議**', () => {
    seed([
      project({
        verification: {
          repo_ok: true,
          working_tree_dirty: false,
          branches: {
            production: {
              branch: 'mian',
              exists: false,
              did_you_mean: ['main'],
            },
          },
        },
      }),
    ])
    render(<CodeProjectsPage />)

    expect(screen.getByText(/分支不存在/)).toBeInTheDocument()
    expect(screen.getByText(/是不是 main？/)).toBeInTheDocument()
  })

  it('驗證整份失敗時顯示錯誤訊息', () => {
    seed([project({ last_verify_error: '找不到這個資料夾' })])
    render(<CodeProjectsPage />)
    expect(screen.getByText('找不到這個資料夾')).toBeInTheDocument()
  })

  it('store 的錯誤走 role="alert"', () => {
    seed([], { error: '路徑不是 git 專案' })
    render(<CodeProjectsPage />)
    expect(screen.getByRole('alert')).toHaveTextContent('路徑不是 git 專案')
  })

  it('每個專案都有重新檢查、編輯、刪除三個動作', async () => {
    const verify = vi.fn(async () => {})
    const remove = vi.fn(async () => true)
    seed([project()], { verify, remove })
    render(<CodeProjectsPage />)

    const row = screen.getByText('智慧客服後端').closest('li') as HTMLElement
    await userEvent.click(within(row).getByRole('button', { name: /重新檢查/ }))
    expect(verify).toHaveBeenCalledWith(1)

    // 刪除鈕只有圖示，說明是它唯一的可及名稱來源
    await userEvent.click(within(row).getByRole('button', { name: '刪除 智慧客服後端' }))
    expect(remove).toHaveBeenCalledWith(1)
  })
})

describe('參考專案：新增／編輯表單', () => {
  it('預設是「新增專案」，填完送出會帶著 draft 呼叫 create', async () => {
    const create = vi.fn(async () => project())
    seed([], { create })
    render(<CodeProjectsPage />)

    expect(screen.getByRole('heading', { name: '新增專案' })).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('專案名稱'), '訂閱系統')
    await userEvent.type(screen.getByLabelText(/專案資料夾路徑/), '/srv/sub')
    await userEvent.type(screen.getByLabelText('正式環境'), 'PROD')

    await userEvent.click(screen.getByRole('button', { name: '新增' }))
    expect(create).toHaveBeenCalledWith({
      name: '訂閱系統',
      repo_path: '/srv/sub',
      branches: { production: 'PROD' },
      default_env: 'production',
    })
  })

  it('三個環境各有一個分支輸入框與一個「設為預設」', () => {
    render(<CodeProjectsPage />)
    for (const label of ['正式環境', 'UAT 環境', '開發環境']) {
      expect(screen.getByLabelText(label)).toBeInTheDocument()
    }
    expect(screen.getAllByRole('radio', { name: '設為預設' })).toHaveLength(3)
  })

  it('**按編輯把那一筆填進表單**，送出走 update 而不是 create', async () => {
    const update = vi.fn(async () => project())
    const create = vi.fn(async () => project())
    seed([project()], { update, create })
    render(<CodeProjectsPage />)

    await userEvent.click(screen.getByRole('button', { name: '編輯' }))

    expect(screen.getByRole('heading', { name: '編輯專案' })).toBeInTheDocument()
    expect(screen.getByLabelText('專案名稱')).toHaveValue('智慧客服後端')
    expect(screen.getByLabelText(/專案資料夾路徑/)).toHaveValue(
      '/Users/cheng/IdeaProjects/cs-backend',
    )
    expect(screen.getByLabelText('正式環境')).toHaveValue('main')

    await userEvent.click(screen.getByRole('button', { name: '儲存' }))
    expect(update).toHaveBeenCalledWith(1, expect.objectContaining({ name: '智慧客服後端' }))
    expect(create).not.toHaveBeenCalled()
  })

  it('編輯中按取消回到「新增專案」的空表單', async () => {
    seed([project()])
    render(<CodeProjectsPage />)

    await userEvent.click(screen.getByRole('button', { name: '編輯' }))
    await userEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(screen.getByRole('heading', { name: '新增專案' })).toBeInTheDocument()
    expect(screen.getByLabelText('專案名稱')).toHaveValue('')
    // 新增模式沒有取消鈕（沒有東西可以取消）
    expect(screen.queryByRole('button', { name: '取消' })).toBeNull()
  })

  it('儲存中把送出鈕鎖住', () => {
    seed([], { saving: true })
    render(<CodeProjectsPage />)
    expect(screen.getByRole('button', { name: '新增' })).toBeDisabled()
  })

  it('**只有一個「參考專案」標題**（拆檔順手修掉的缺陷）', () => {
    // 拆檔前 CodeProjectsPage 是薄殼、包著 CodeProjectSettings，兩層各畫一個
    // 同名 h2。畫面上看起來只是標題重複，對螢幕閱讀器則是「兩個同名的區段」
    // ——用標題導覽時完全分不出該進哪一個。
    // **這一條在重構前是紅的**（會找到 2 個），是新行為不是特徵測試。
    seed([])
    render(<CodeProjectsPage />)
    expect(screen.getAllByRole('heading', { name: '參考專案' })).toHaveLength(1)
  })

  it('**渲染結果裡 [title] 選得到 0 個**（規格 §10.3）', () => {
    seed([project({ last_verify_error: '找不到這個資料夾' })])
    const { container } = render(<CodeProjectsPage />)
    expect(container.querySelectorAll('[title]')).toHaveLength(0)
  })
})
