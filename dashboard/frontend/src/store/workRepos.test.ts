import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import {
  draftFromWorkRepo,
  emptyWorkRepoDraft,
  formatList,
  parseList,
  useWorkRepoStore,
  validateWorkRepoDraft,
} from './workRepos'
import type { WorkRepo } from '@/lib/types'

/**
 * 「我的工作 repo」store 的單元測試（Draft Worklog，ADR-0008）。
 *
 * 這裡守的三件事，壞掉的表現都**不是報錯**：
 *
 * 1. **相對路徑**：後端會相對於伺服器的工作目錄去解，登錄會「成功」，但撈的
 *    是另一個地方；擋在前端能在使用者還看著輸入框的時候講清楚。
 * 2. **清空預設 ZPlanner 專案**：`PATCH` 上「沒帶」是不改、`null` 才是清除。
 *    把空字串當成「省略」會讓「我不要預設專案了」這個動作**永遠存不進去**，
 *    而且畫面重新載入前看起來像成功了。
 * 3. **陣列欄位整組取代**：`author_emails` 送過去的必須是完整清單，
 *    否則「把某個舊信箱拿掉」沒有辦法表達。
 */

function workRepo(over: Partial<WorkRepo> = {}): WorkRepo {
  return {
    id: 1,
    name: '智慧客服後端',
    repo_path: '/Users/cheng/repos/cs-backend',
    author_emails: ['me@corp.com'],
    branch_filter: [],
    default_zplanner_project_id: 42,
    default_zplanner_project_key: 'PEI',
    enabled: true,
    ...over,
  }
}

beforeEach(() => {
  vi.restoreAllMocks()
  useWorkRepoStore.setState({
    repos: [],
    loading: false,
    saving: false,
    error: null,
    loaded: false,
    verifyingId: null,
  })
})

describe('parseList / formatList', () => {
  it('換行與逗號都算分隔，去空白、丟掉空項', () => {
    expect(parseList(' a@x.com ,\n b@y.com \n\n')).toEqual(['a@x.com', 'b@y.com'])
  })

  it('**去重但保留順序**——第一個通常是主要信箱，重排會讓人看到自己沒填過的清單', () => {
    expect(parseList('b@y.com\na@x.com\nb@y.com')).toEqual(['b@y.com', 'a@x.com'])
  })

  it('全空字串是空陣列（＝不限作者，不是「一個空信箱」）', () => {
    expect(parseList('  \n , \n')).toEqual([])
  })

  it('formatList 一行一筆，肉眼數得出來有幾個', () => {
    expect(formatList(['a@x.com', 'b@y.com'])).toBe('a@x.com\nb@y.com')
  })
})

describe('validateWorkRepoDraft', () => {
  const draft = (over: Partial<ReturnType<typeof emptyWorkRepoDraft>> = {}) => ({
    ...emptyWorkRepoDraft(),
    name: '訂閱系統',
    repo_path: '/srv/sub',
    ...over,
  })

  it('名稱與路徑是必填', () => {
    expect(validateWorkRepoDraft(draft({ name: ' ' }))).toBe('請填寫名稱')
    expect(validateWorkRepoDraft(draft({ repo_path: ' ' }))).toBe('請填寫 repo 路徑')
  })

  it('**相對路徑擋下來**：它會相對於伺服器的工作目錄去解', () => {
    expect(validateWorkRepoDraft(draft({ repo_path: 'repos/sub' }))).toBe(
      'repo 路徑必須是絕對路徑',
    )
  })

  it('Windows 的絕對路徑與 UNC 路徑都放行', () => {
    expect(validateWorkRepoDraft(draft({ repo_path: 'D:\\work\\sub' }))).toBeNull()
    expect(validateWorkRepoDraft(draft({ repo_path: '\\\\nas\\work\\sub' }))).toBeNull()
  })

  it('**作者信箱與分支留空都不擋**——那是後端允許、而且文件建議的流程', () => {
    expect(validateWorkRepoDraft(draft({ author_emails: '', branch_filter: '' }))).toBeNull()
  })

  it('ZPlanner 專案 ID 必須是數字', () => {
    expect(validateWorkRepoDraft(draft({ default_zplanner_project_id: 'PEI' }))).toBe(
      'ZPlanner 專案 ID 必須是數字',
    )
  })
})

describe('draftFromWorkRepo', () => {
  it('把既有的一筆攤成表單值', () => {
    const d = draftFromWorkRepo(workRepo({ branch_filter: ['main', 'develop'] }))
    expect(d.author_emails).toBe('me@corp.com')
    expect(d.branch_filter).toBe('main\ndevelop')
    expect(d.default_zplanner_project_id).toBe('42')
    expect(d.default_zplanner_project_key).toBe('PEI')
  })

  it('沒有預設專案時是空字串，不是字串 "null"', () => {
    const d = draftFromWorkRepo(
      workRepo({ default_zplanner_project_id: null, default_zplanner_project_key: null }),
    )
    expect(d.default_zplanner_project_id).toBe('')
    expect(d.default_zplanner_project_key).toBe('')
  })

  it('**seedEmails 會接在既有信箱後面並去重**（點 detected_emails 加進來的那條路）', () => {
    const d = draftFromWorkRepo(workRepo(), ['me@corp.com', 'me@personal.dev'])
    expect(d.author_emails).toBe('me@corp.com\nme@personal.dev')
  })
})

describe('送出的 body', () => {
  it('**清空預設專案送的是 null 不是省略**——省略在 PATCH 上是「不改」', async () => {
    const spy = vi.spyOn(api, 'updateWorkRepo').mockResolvedValue(workRepo())
    useWorkRepoStore.setState({ repos: [workRepo()] })

    await useWorkRepoStore.getState().update(1, {
      ...draftFromWorkRepo(workRepo()),
      default_zplanner_project_id: '',
      default_zplanner_project_key: '',
    })

    expect(spy).toHaveBeenCalledWith(
      1,
      expect.objectContaining({
        default_zplanner_project_id: null,
        default_zplanner_project_key: null,
      }),
    )
  })

  it('**陣列欄位整組送出**，才表達得出「把某個舊信箱拿掉」', async () => {
    const spy = vi.spyOn(api, 'updateWorkRepo').mockResolvedValue(workRepo())
    useWorkRepoStore.setState({ repos: [workRepo({ author_emails: ['a@x.com', 'b@y.com'] })] })

    await useWorkRepoStore.getState().update(1, {
      ...draftFromWorkRepo(workRepo()),
      author_emails: 'a@x.com',
    })

    expect(spy).toHaveBeenCalledWith(1, expect.objectContaining({ author_emails: ['a@x.com'] }))
  })

  it('新增時空的作者信箱送空陣列（後端允許，後果由畫面上的警告負責）', async () => {
    const spy = vi.spyOn(api, 'createWorkRepo').mockResolvedValue(workRepo())

    await useWorkRepoStore.getState().create({
      ...emptyWorkRepoDraft(),
      name: '訂閱系統',
      repo_path: '/srv/sub',
    })

    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ author_emails: [], branch_filter: [] }),
    )
  })
})

describe('verification 的保存', () => {
  it('**編輯不會洗掉剛才驗證出來的 detected_emails**', async () => {
    const verified = workRepo({
      verification: { ok: true, error: null, head_sha: 'a3f91c2', detected_emails: ['x@y.com'] },
    })
    useWorkRepoStore.setState({ repos: [verified] })
    // PATCH 的回覆一定帶 verification，但這裡刻意模擬「沒帶」來驗保護邏輯本身
    vi.spyOn(api, 'updateWorkRepo').mockResolvedValue(workRepo({ name: '改過的名字' }))

    await useWorkRepoStore.getState().update(1, draftFromWorkRepo(verified))

    const after = useWorkRepoStore.getState().repos[0]
    expect(after.name).toBe('改過的名字')
    expect(after.verification?.detected_emails).toEqual(['x@y.com'])
  })

  it('ensureLoaded 載入過就不再打 API', async () => {
    const spy = vi.spyOn(api, 'workRepos').mockResolvedValue({ repos: [] })
    await useWorkRepoStore.getState().ensureLoaded()
    await useWorkRepoStore.getState().ensureLoaded()
    expect(spy).toHaveBeenCalledTimes(1)
  })
})
