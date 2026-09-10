import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PersonasPage } from './PersonasPage'
import { useReplySettingsStore } from '@/store/replySettings'
import type { Persona } from '@/lib/types'

/**
 * Persona 設定頁。
 *
 * **這一份的主角是「這一頁自己會不會去載資料」。** 其他設定頁都會
 * （`CodeProjectsPage` 走 `ensureLoaded`、`ReplyDefaultsPage` 有
 * `useEffect(() => void load())`），只有這一頁原本什麼都不做，直接讀
 * `store.personas`。後果是**直接落在這一頁**的三條路徑——貼
 * `#/settings/personas` 深連結、停在這一頁按重新整理、從書籤進來——
 * 都會看到「還沒有匯入任何 Persona。」，而資料庫裡明明有。
 *
 * 那個畫面與「Persona 被刪掉了」長得一模一樣，這是最糟的失敗形態：
 * 使用者會重新匯入一次，然後因為 `UNIQUE(viewer_id, name)` 走進
 * 「同名視為更新」那條路，看起來像成功了，於是永遠不會知道原本那筆還在。
 *
 * 2026-09-11 實測（瀏覽器，正對照見下）：
 *   - 冷啟動直接進 `#/settings/personas` → 「已匯入（0）」
 *   - 先逛 `#/settings/reply`（它會 load）再回來 → 「已匯入（2）」  ← 資料在
 *   - 停在這一頁按重新整理 → 又變「已匯入（0）」
 */

function persona(over: Partial<Persona> = {}): Persona {
  return {
    id: 1,
    name: '羅振宇（羅胖）',
    description: '罗振宇（罗胖）的思维框架与表达方式。',
    source_type: 'github',
    source_repository: 'fxp/persona-distill-skills',
    source_commit_sha: '24c9850e4a8bbb8b3b1ab797b428163fa3c07066',
    source_ref: 'main',
    enabled: true,
    imported_at: '2026-09-10T17:05:49+00:00',
    created_at: '2026-09-10T17:05:49+00:00',
    updated_at: '2026-09-10T17:05:49+00:00',
    profile: {
      name: 'luozhenyu-perspective',
      thinking_style: ['把問題放到更長的時間尺度上看'],
      communication_style: ['先給一個反直覺的結論，再拆解'],
      response_preferences: {},
      avoid: [],
      boundaries: ['具体投资建议——他的框架是宏观方向'],
    },
    ...over,
  }
}

/** seed store，並且**不**讓 load 真的打 API——回傳那顆 spy 以便斷言。 */
function seed(personas: Persona[], over: Record<string, unknown> = {}) {
  const load = vi.fn(async () => {})
  useReplySettingsStore.setState({
    personas,
    sources: [],
    loading: false,
    loaded: personas.length > 0,
    busy: false,
    error: null,
    load,
    ...over,
  })
  return load
}

beforeEach(() => {
  seed([])
})

describe('Persona 設定頁：自己會去載資料', () => {
  it('**掛載時就呼叫 store.load()**（否則深連結／重新整理會看到假的空狀態）', async () => {
    const load = seed([], { loaded: false })
    render(<PersonasPage />)
    await waitFor(() => expect(load).toHaveBeenCalled())
  })

  it('store 還沒載完時不說「還沒有匯入任何 Persona」——那是假的空狀態', () => {
    seed([], { loaded: false, loading: true })
    render(<PersonasPage />)
    expect(screen.queryByText(/還沒有匯入任何 Persona/)).toBeNull()
  })

  it('載完確實是空的，才顯示空狀態', () => {
    seed([], { loaded: true, loading: false })
    render(<PersonasPage />)
    expect(screen.getByText(/還沒有匯入任何 Persona/)).toBeInTheDocument()
  })
})

describe('Persona 設定頁：匯入失敗的原因留在畫面上', () => {
  /**
   * 後端的 409 PERSONA_INVALID 現在會帶 `describe_unusable()` 的診斷
   * （「這份檔案讀到哪些章節、可用的章節名是什麼」）。那是要**照著改**的
   * 資訊，而 sonner 的 toast 預設 4 秒就收掉——算得出診斷卻只放在 toast 裡，
   * 跟沒算差不多。所以它必須留在表單下方。
   */
  const DIAGNOSIS =
    '這份來源淨化之後沒有留下任何可用的風格資訊。這份檔案讀到的章節是「Overview」，' +
    '都不在可用清單裡。可用的章節名例如：思考方式（心智模型／思考框架）。'

  it('**匯入失敗後診斷訊息留在畫面上**（不是只閃一下 toast）', async () => {
    seed([], {
      loaded: true,
      importPersona: async () => {
        useReplySettingsStore.setState({ error: DIAGNOSIS })
        return null
      },
    })
    render(<PersonasPage />)

    // Repository 模式要兩欄都填才按得下去，用網址模式最短
    await userEvent.click(screen.getByRole('button', { name: '網址' }))
    await userEvent.type(screen.getByLabelText(/檔案網址/), 'https://example.invalid/a.md')
    await userEvent.click(screen.getByRole('button', { name: /匯入/ }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/不在可用清單裡/)
    expect(alert).toHaveTextContent(/心智模型/)
  })

  it('換模式時清掉上一個模式的錯誤（它講的是另一種輸入的問題）', async () => {
    seed([], {
      loaded: true,
      importPersona: async () => {
        useReplySettingsStore.setState({ error: DIAGNOSIS })
        return null
      },
    })
    render(<PersonasPage />)

    await userEvent.click(screen.getByRole('button', { name: '網址' }))
    await userEvent.type(screen.getByLabelText(/檔案網址/), 'https://example.invalid/a.md')
    await userEvent.click(screen.getByRole('button', { name: /匯入/ }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Repository' }))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('成功之後不留著上一次的錯誤', async () => {
    let shouldFail = true
    seed([], {
      loaded: true,
      importPersona: async () => {
        if (shouldFail) {
          useReplySettingsStore.setState({ error: DIAGNOSIS })
          return null
        }
        return { persona: persona(), notice: null }
      },
    })
    render(<PersonasPage />)

    await userEvent.click(screen.getByRole('button', { name: '網址' }))
    await userEvent.type(screen.getByLabelText(/檔案網址/), 'https://example.invalid/a.md')
    await userEvent.click(screen.getByRole('button', { name: /匯入/ }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()

    shouldFail = false
    await userEvent.type(screen.getByLabelText(/檔案網址/), 'x')
    await userEvent.click(screen.getByRole('button', { name: /匯入/ }))
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    // **正對照**：成功路徑真的跑到底了，不是 handleImport 中途爆掉才沒有
    // alert。清空輸入框排在 `toast.success(result.persona.name)` 後面，
    // 所以它空了就證明整段跑完——這正是 EvidenceList 那份 fixture 踩過的坑：
    // 回傳形狀錯了，測試照樣綠。
    await waitFor(() => expect(screen.getByLabelText(/檔案網址/)).toHaveValue(''))
  })
})

describe('Persona 設定頁：從 repo 根目錄匯入時提醒一句', () => {
  /**
   * 後端的 `_persona_import_notice` 會在「網址模式 ＋ 根目錄檔案」時回一句
   * 提醒。這裡守的是「它有被畫出來」以及**它不是 alert**——匯入是成功的，
   * 用打斷式播報（role="alert" 隱含 assertive）會過度。
   */
  const NOTICE = '這份是從 repo 根目錄的 SKILL.md 匯入的。有些 repo 根目錄放的是「如何寫 persona」的方法論。'

  async function importWithNotice(notice: string | null) {
    seed([], {
      loaded: true,
      importPersona: async () => ({ persona: persona(), notice }),
    })
    render(<PersonasPage />)
    await userEvent.click(screen.getByRole('button', { name: '網址' }))
    await userEvent.type(screen.getByLabelText(/檔案網址/), 'https://example.invalid/SKILL.md')
    await userEvent.click(screen.getByRole('button', { name: /匯入/ }))
  }

  it('**後端給了 notice 就畫在畫面上**', async () => {
    await importWithNotice(NOTICE)
    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent(/根目錄/)
    expect(status).toHaveTextContent(/方法論/)
  })

  it('提醒不是 alert（匯入成功，不該用打斷式播報）', async () => {
    await importWithNotice(NOTICE)
    await screen.findByRole('status')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('正對照：沒有 notice 時不畫（不是永遠畫一塊空的）', async () => {
    await importWithNotice(null)
    await waitFor(() => expect(screen.getByText(/已匯入/)).toBeInTheDocument())
    expect(screen.queryByRole('status')).toBeNull()
  })
})

describe('Persona 設定頁：已匯入清單', () => {
  it('計數與名稱讀得出來', () => {
    seed([persona(), persona({ id: 2, name: '香帥（唐涯）' })])
    render(<PersonasPage />)
    expect(screen.getByText('已匯入（2）')).toBeInTheDocument()
    expect(screen.getByText('羅振宇（羅胖）')).toBeInTheDocument()
    expect(screen.getByText('香帥（唐涯）')).toBeInTheDocument()
  })
})
