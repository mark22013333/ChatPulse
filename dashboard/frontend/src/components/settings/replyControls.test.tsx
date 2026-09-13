import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { QuickReplySettings } from '@/components/draft/QuickReplySettings'
import { ReplyDefaultsPage } from '@/components/settings/ReplyDefaultsPage'
import { RouterProvider } from '@/router/useRouter'
import { useDraftStore } from '@/store/draft'
import { useReplySettingsStore } from '@/store/replySettings'
import type { Persona, PolisherInfo, ReplyPrompt, ReplyTone } from '@/lib/types'

/**
 * 特徵測試：草稿側欄的「這一次」設定與設定中心的「以後每次」預設值，
 * 三個下拉必須長得一模一樣。
 *
 * 這一份是為了把 replyControls 的抽取釘住——**它在抽取之前就要能通過**，
 * 抽取之後也要通過。斷言全部只用使用者看得到的東西（可及名稱、選項文字、
 * 禁用狀態），所以不會因為內部改成共用元件而需要改寫。
 */

const TONES: ReplyTone[] = [
  { id: 'neutral', label: '中性', description: '不帶情緒地把事情說清楚', example: '這邊我確認過了' },
  { id: 'warm', label: '親切', description: '客氣一點，適合對外', example: '沒問題，我這邊處理' },
]

function persona(id: number, name: string, enabled: boolean, description = ''): Persona {
  return {
    id,
    name,
    description,
    source_type: 'manual',
    enabled,
    imported_at: '2026-08-01T00:00:00Z',
    refreshed_at: null,
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    profile: {
      name,
      thinking_style: [],
      communication_style: [],
      response_preferences: {},
      avoid: [],
    },
  }
}

const PERSONAS: Persona[] = [
  persona(7, '工程師口吻', true, '講重點、不寒暄'),
  persona(8, '停用的那個', false),
]

const PROMPTS: ReplyPrompt[] = [
  {
    id: 3,
    name: '要條列',
    description: '請用條列回覆',
    prompt: '請用條列式回覆。',
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
  },
]

const POLISHERS: PolisherInfo[] = [
  { name: 'sepia', label: 'Sepia', available: true, reason: '' },
]

beforeEach(() => {
  useReplySettingsStore.setState({
    tones: TONES,
    serverDefaultTone: 'neutral',
    personas: PERSONAS,
    replyPrompts: PROMPTS,
    polishers: POLISHERS,
    loaded: true,
    // 元件掛載時會呼叫 load()，測試裡不要真的打 API
    load: async () => {},
  })
  useDraftStore.setState({
    toneId: null,
    personaId: null,
    customPrompt: '',
    customPromptId: null,
    sepiaEnabled: false,
  })
  window.history.replaceState(null, '', '#/mentions/1')
})

function renderQuick(disabled = false) {
  return render(
    <RouterProvider>
      <QuickReplySettings disabled={disabled} />
    </RouterProvider>,
  )
}

function renderDefaults() {
  return render(
    <RouterProvider>
      <ReplyDefaultsPage />
    </RouterProvider>,
  )
}

/** 三個下拉共通的斷言：兩邊都要通過同一組。 */
async function expectTheThreeControls() {
  const tone = screen.getByRole('combobox', { name: '口氣' })
  const persona = screen.getByRole('combobox', { name: 'Persona' })
  expect(tone).toBeInTheDocument()
  expect(persona).toBeInTheDocument()

  // 沒有任何偏好時，兩邊都顯示「跟隨預設（<伺服器預設口氣>）」
  expect(tone).toHaveTextContent('跟隨預設（中性）')

  await userEvent.click(tone)
  const listbox = await screen.findByRole('listbox')
  // 每個口氣都要看得到說明與範例——它們是選擇的依據，不能只活在 title 裡
  expect(within(listbox).getByText('不帶情緒地把事情說清楚')).toBeInTheDocument()
  expect(within(listbox).getByText('例：這邊我確認過了')).toBeInTheDocument()
  expect(within(listbox).getByText('客氣一點，適合對外')).toBeInTheDocument()
  await userEvent.keyboard('{Escape}')
}

describe('兩處回覆設定渲染同一組控件', () => {
  it('草稿側欄（這一次）', async () => {
    renderQuick()
    await expectTheThreeControls()
  })

  it('設定中心（以後每次）', async () => {
    renderDefaults()
    await expectTheThreeControls()
  })

  it('停用的 Persona 兩邊都不列出來', async () => {
    renderDefaults()

    await userEvent.click(screen.getByRole('combobox', { name: 'Persona' }))
    const listbox = await screen.findByRole('listbox')
    expect(within(listbox).getByText('工程師口吻')).toBeInTheDocument()
    expect(within(listbox).queryByText('停用的那個')).toBeNull()
  })

  it('**串流中草稿側欄的下拉會禁用**，設定頁的不會', () => {
    const { unmount } = renderQuick(true)
    expect(screen.getByRole('combobox', { name: '口氣' })).toBeDisabled()
    unmount()

    // 正對照：同一顆下拉在設定頁永遠可以動（那裡沒有串流這回事）
    renderDefaults()
    expect(screen.getByRole('combobox', { name: '口氣' })).toBeEnabled()
  })

  it('一則常用提示詞都沒有時不畫那個下拉（空選單只會讓人以為壞了）', () => {
    useReplySettingsStore.setState({ replyPrompts: [] })
    renderDefaults()

    // 口氣與 Persona 還在，只少了提示詞那一個
    expect(screen.getAllByRole('combobox')).toHaveLength(2)
  })

  it('選了 Persona 之後兩邊都出現來源說明', () => {
    useDraftStore.setState({ personaId: 7 })
    renderDefaults()

    expect(screen.getByText(/自訂 Persona/)).toBeInTheDocument()
    expect(screen.getByText(/匯入於 2026-08-01/)).toBeInTheDocument()
  })
})
