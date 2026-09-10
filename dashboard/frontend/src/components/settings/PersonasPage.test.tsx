import { render, screen, waitFor } from '@testing-library/react'
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

describe('Persona 設定頁：已匯入清單', () => {
  it('計數與名稱讀得出來', () => {
    seed([persona(), persona({ id: 2, name: '香帥（唐涯）' })])
    render(<PersonasPage />)
    expect(screen.getByText('已匯入（2）')).toBeInTheDocument()
    expect(screen.getByText('羅振宇（羅胖）')).toBeInTheDocument()
    expect(screen.getByText('香帥（唐涯）')).toBeInTheDocument()
  })
})
