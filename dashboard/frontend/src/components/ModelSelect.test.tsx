import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { ModelSelect } from '@/components/ModelSelect'
import { ProviderSelect } from '@/components/ProviderSelect'
import { AUTO_PROVIDER, INHERIT_MODEL, modelRequestField, useProviderStore } from '@/store/providers'
import type { AIProvider } from '@/lib/types'

/**
 * Model 選擇器的行為測試。
 *
 * 斷言只用 Viewer 看得到的東西（可及名稱、選項文字、禁用狀態、輸入框），
 * 外加 `modelRequestField()` 這個「實際會送出什麼」的出口——後者才是這個
 * 元件存在的目的，光看畫面正確不代表送出去的值正確。
 */

const PROVIDERS: AIProvider[] = [
  {
    name: 'claude_cli',
    label: 'Claude Code（本機 CLI）',
    model: 'claude-cli:opus',
    model_id: 'opus',
    models: ['opus', 'sonnet', 'haiku'],
    available: true,
    reason: '使用本機 claude',
  },
  {
    name: 'gemini',
    label: 'Gemini（Google AI Studio）',
    model: 'gemini-3.6-flash',
    model_id: 'gemini-3.6-flash',
    models: ['gemini-3.6-flash'],
    available: true,
    reason: '使用 GOOGLE_API_KEY',
  },
]

beforeEach(() => {
  useProviderStore.setState({
    providers: PROVIDERS,
    serverDefault: 'claude',
    savedDefault: null,
    selected: 'claude_cli',
    savedModels: {},
    modelSelection: {},
    modelDrafts: {},
    initialised: true,
    loading: false,
    saving: false,
    savingModel: false,
    error: null,
  })
})

function renderModelSelect() {
  return render(<ModelSelect id="test-model" />)
}

/** 兩個下拉一起畫：切換供應商要靠真的點 `ProviderSelect` 才算驗到那條路。 */
function renderBoth() {
  return render(
    <div>
      <ProviderSelect id="test-provider" />
      <ModelSelect id="test-model" />
    </div>,
  )
}

function modelTrigger() {
  return screen.getByRole('combobox', { name: '模型' })
}

/** 打開某個下拉、點掉其中一個選項。 */
async function pickOption(trigger: HTMLElement, optionText: string | RegExp) {
  await userEvent.click(trigger)
  const listbox = await screen.findByRole('listbox')
  await userEvent.click(within(listbox).getByText(optionText))
}

describe('ModelSelect', () => {
  it('🔴 供應商選「自動」時停用，而且說得出為什麼', () => {
    useProviderStore.setState({ selected: AUTO_PROVIDER })
    renderModelSelect()

    expect(modelTrigger()).toBeDisabled()
    // 只變灰不算：理由要是看得見的文字，否則 Viewer 不知道要先去改供應商
    expect(screen.getByText(/供應商選「自動」時不能指定 model/)).toBeInTheDocument()
  })

  it('選了具體供應商時可以動，而且不會留著「自動」那段說明', () => {
    renderModelSelect()

    expect(modelTrigger()).toBeEnabled()
    expect(screen.queryByText(/供應商選「自動」時不能指定 model/)).toBeNull()
  })

  it('預設是「跟隨預設」，並寫出目前生效的裸值', () => {
    renderModelSelect()

    expect(modelTrigger()).toHaveTextContent('跟隨預設（opus）')
    expect(modelRequestField()).toEqual({})
  })

  it('下拉內容＝該供應商的常用清單＋「自訂…」', async () => {
    renderModelSelect()

    await userEvent.click(modelTrigger())
    const listbox = await screen.findByRole('listbox')
    for (const model of ['opus', 'sonnet', 'haiku']) {
      expect(within(listbox).getByText(model)).toBeInTheDocument()
    }
    expect(within(listbox).getByText('自訂…')).toBeInTheDocument()
    // Gemini 的 model 不該出現在 Claude 的清單裡
    expect(within(listbox).queryByText('gemini-3.6-flash')).toBeNull()
  })

  it('選了清單裡的 model，送出時 body 帶的是裸值', async () => {
    renderModelSelect()

    await pickOption(modelTrigger(), 'sonnet')

    expect(modelTrigger()).toHaveTextContent('sonnet')
    expect(modelRequestField()).toEqual({ model: 'sonnet' })
  })

  it('選「自訂…」會出現輸入框，打進去的字就是送出的裸值', async () => {
    renderModelSelect()

    expect(screen.queryByRole('textbox', { name: '自訂 model 名稱' })).toBeNull()

    await pickOption(modelTrigger(), '自訂…')

    const input = await screen.findByRole('textbox', { name: '自訂 model 名稱' })
    await userEvent.type(input, 'claude-fable-5')

    expect(modelRequestField()).toEqual({ model: 'claude-fable-5' })
  })

  it('🔴 選了自訂卻把框清空時，Viewer 看得出這次其實沒有指定 model', async () => {
    renderModelSelect()

    await pickOption(modelTrigger(), '自訂…')
    const input = await screen.findByRole('textbox', { name: '自訂 model 名稱' })
    await userEvent.type(input, 'claude-fable-5')
    expect(modelRequestField()).toEqual({ model: 'claude-fable-5' })

    await userEvent.clear(input)

    // 實際行為：不帶 model，退回供應商的預設
    expect(modelRequestField()).toEqual({})
    // 畫面要說得出來——觸發器不可以停在看起來已經設定好的「自訂…」
    expect(modelTrigger()).toHaveTextContent('尚未填')
    const hint = screen.getByText(/還沒填 model 名稱，這次不會指定 model/)
    // 而且要講出實際會用哪一個，不是只說「沒設定」
    expect(hint).toHaveTextContent('實際會用 opus')
  })

  it('自訂框填回內容之後，那段提醒要跟著消失（不能一直掛著嚇人）', async () => {
    renderModelSelect()

    await pickOption(modelTrigger(), '自訂…')
    const input = await screen.findByRole('textbox', { name: '自訂 model 名稱' })
    expect(screen.getByText(/還沒填 model 名稱/)).toBeInTheDocument()

    await userEvent.type(input, 'sonnet-4')

    expect(screen.queryByText(/還沒填 model 名稱/)).toBeNull()
    expect(modelTrigger()).toHaveTextContent('自訂：sonnet-4')
  })

  /**
   * 「不帶 model」與「偏好會被套用」兩半各自都有測試，**組合起來這條沒有**
   * ——缺陷就活在那個縫裡：畫面寫 config 預設（opus），實際跑的是偏好（sonnet）。
   *
   * 判準只有一條：括號裡的值 ＝ 真的會跑起來的值。後端生效順序是
   * body → 偏好 → config 預設（契約第三節），所以有偏好時要顯示偏好。
   */
  describe('已存偏好時，畫面顯示的值必須是實際會生效的那一個', () => {
    it('「跟隨預設」的括號寫偏好（sonnet），不是 config 預設（opus）', () => {
      useProviderStore.setState({
        savedModels: { claude_cli: 'sonnet' },
        modelSelection: { claude_cli: INHERIT_MODEL },
      })
      renderModelSelect()

      expect(modelTrigger()).toHaveTextContent('跟隨預設（sonnet）')
      expect(modelTrigger()).not.toHaveTextContent('opus')
      // 仍然不帶 model——後端會自己套用偏好，這一半的行為不變
      expect(modelRequestField()).toEqual({})
    })

    it('自訂框空白時的提示也寫偏好（sonnet）', async () => {
      useProviderStore.setState({ savedModels: { claude_cli: 'sonnet' } })
      renderModelSelect()

      await pickOption(modelTrigger(), '自訂…')

      const hint = screen.getByText(/還沒填 model 名稱/)
      expect(hint).toHaveTextContent('實際會用 sonnet')
      expect(hint).not.toHaveTextContent('opus')
      expect(modelRequestField()).toEqual({})
    })

    it('正對照：沒有偏好時才寫 config 預設（opus）', () => {
      useProviderStore.setState({ modelSelection: { claude_cli: INHERIT_MODEL } })
      renderModelSelect()

      expect(modelTrigger()).toHaveTextContent('跟隨預設（opus）')
    })
  })

  it('切換供應商時，model 跟著切到該供應商的已存偏好', async () => {
    useProviderStore.setState({ savedModels: { claude_cli: 'haiku', gemini: 'gemini-3.6-flash' } })
    renderBoth()

    expect(modelTrigger()).toHaveTextContent('haiku')

    await pickOption(screen.getByRole('combobox', { name: 'AI 供應商' }), 'Gemini（Google AI Studio）')

    expect(modelTrigger()).toHaveTextContent('gemini-3.6-flash')
    expect(modelRequestField()).toEqual({ model: 'gemini-3.6-flash' })
  })

  it('切到沒有偏好的供應商時回到「跟隨預設」，不會把上一個的 model 帶過去', async () => {
    useProviderStore.setState({ savedModels: { claude_cli: 'haiku' } })
    renderBoth()

    await pickOption(screen.getByRole('combobox', { name: 'AI 供應商' }), 'Gemini（Google AI Studio）')

    expect(modelTrigger()).toHaveTextContent('跟隨預設（gemini-3.6-flash）')
    expect(modelRequestField()).toEqual({})
  })

  it('偏好是清單外的裸值時，開啟就停在「自訂」而且框裡留著那個值', () => {
    useProviderStore.setState({ savedModels: { claude_cli: 'claude-fable-5' } })
    renderModelSelect()

    expect(screen.getByRole('textbox', { name: '自訂 model 名稱' })).toHaveValue('claude-fable-5')
    expect(modelRequestField()).toEqual({ model: 'claude-fable-5' })
  })

  it('串流中不讓改（下拉與自訂輸入框都要鎖）', () => {
    useProviderStore.setState({
      modelSelection: { claude_cli: '__custom__' },
      modelDrafts: { claude_cli: 'claude-fable-5' },
    })
    render(<ModelSelect id="test-model" disabled />)

    expect(modelTrigger()).toBeDisabled()
    expect(screen.getByRole('textbox', { name: '自訂 model 名稱' })).toBeDisabled()
  })

  it('選擇與已存偏好不同時才出現「設為預設」', async () => {
    useProviderStore.setState({ savedModels: { claude_cli: 'sonnet' } })
    renderModelSelect()

    // 目前選的就是已存的偏好，沒有什麼好存的
    expect(screen.queryByRole('button', { name: /設為預設/ })).toBeNull()

    await pickOption(modelTrigger(), 'haiku')

    expect(screen.getByRole('button', { name: /設為預設/ })).toBeInTheDocument()
  })
})
