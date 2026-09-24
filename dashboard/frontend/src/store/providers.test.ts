import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import {
  AUTO_PROVIDER,
  CUSTOM_MODEL,
  INHERIT_MODEL,
  modelRequestField,
  providerLabel,
  providerRequestField,
  resolveModelDraft,
  resolveModelValue,
  resolveSelected,
  useProviderStore,
} from './providers'
import type { AIProvider } from '@/lib/types'

// `model`（帶前綴的識別字）與 `model_id`（裸值）刻意不同，這正是兩者不可混用的地方
const PROVIDERS: AIProvider[] = [
  { name: 'claude_cli', label: 'Claude Code（本機 CLI）', model: 'claude-cli:opus', available: true, reason: '使用本機 claude', model_id: 'opus', models: ['opus', 'sonnet', 'haiku'] },
  { name: 'gemini', label: 'Gemini（Google AI Studio）', model: 'gemini-3.6-flash', available: true, reason: '使用 GOOGLE_API_KEY', model_id: 'gemini-3.6-flash', models: ['gemini-3.6-flash'] },
]

/** 同一份清單，但 gemini 不可用（現實情境：這台機器沒設 GOOGLE_API_KEY）。 */
const WITH_UNAVAILABLE: AIProvider[] = [
  PROVIDERS[0],
  { ...PROVIDERS[1], available: false, reason: '缺 GOOGLE_API_KEY' },
]

beforeEach(() => {
  useProviderStore.setState({
    providers: [],
    serverDefault: null,
    savedDefault: null,
    selected: AUTO_PROVIDER,
    savedModels: {},
    modelSelection: {},
    modelDrafts: {},
    initialised: false,
    loading: false,
    saving: false,
    savingModel: false,
    error: null,
  })
})

describe('resolveSelected', () => {
  it('偏好優先於伺服器預設', () => {
    expect(resolveSelected(PROVIDERS, 'gemini', 'claude_cli')).toBe('claude_cli')
  })

  it('沒有偏好時用伺服器預設', () => {
    expect(resolveSelected(PROVIDERS, 'gemini', null)).toBe('gemini')
  })

  it('伺服器預設是別名（claude／auto）時退回自動，不會選到一個不存在的值', () => {
    expect(resolveSelected(PROVIDERS, 'claude', null)).toBe(AUTO_PROVIDER)
    expect(resolveSelected(PROVIDERS, 'auto', null)).toBe(AUTO_PROVIDER)
  })

  it('偏好指到已不可用的供應商時退回自動', () => {
    expect(resolveSelected(WITH_UNAVAILABLE, 'claude_cli', 'gemini')).toBe(AUTO_PROVIDER)
  })

  it('偏好指到已經不存在的供應商時退回自動（例：舊偏好裡的 claude_api）', () => {
    expect(resolveSelected(PROVIDERS, 'claude_cli', 'claude_api')).toBe(AUTO_PROVIDER)
  })
})

describe('applyServerConfig', () => {
  it('第一次套用會依偏好決定選擇', () => {
    useProviderStore.getState().applyServerConfig({ default: 'claude', providers: PROVIDERS }, 'gemini')
    expect(useProviderStore.getState().selected).toBe('gemini')
    expect(useProviderStore.getState().providers).toHaveLength(2)
  })

  it('之後 /me 再刷新不可以蓋掉使用者當下的選擇', () => {
    const store = useProviderStore.getState()
    store.applyServerConfig({ default: 'claude', providers: PROVIDERS }, null)
    useProviderStore.getState().setSelected('claude_cli')
    useProviderStore.getState().applyServerConfig({ default: 'claude', providers: PROVIDERS }, 'gemini')
    expect(useProviderStore.getState().selected).toBe('claude_cli')
  })
})

describe('providerRequestField', () => {
  it('選「自動」時不帶 provider 欄位（語意＝交給伺服器決定）', () => {
    expect(providerRequestField()).toEqual({})
    expect('provider' in providerRequestField()).toBe(false)
  })

  it('選了具體供應商就帶進 body', () => {
    useProviderStore.getState().applyServerConfig({ default: 'claude', providers: PROVIDERS }, null)
    useProviderStore.getState().setSelected('claude_cli')
    expect(providerRequestField()).toEqual({ provider: 'claude_cli' })
  })
})

describe('resolveModelValue', () => {
  it('沒選過也沒偏好時是「跟隨預設」（不指定 model）', () => {
    expect(resolveModelValue(PROVIDERS, {}, {}, 'claude_cli')).toBe(INHERIT_MODEL)
  })

  it('有偏好、而且在常用清單裡就選它', () => {
    expect(resolveModelValue(PROVIDERS, { claude_cli: 'sonnet' }, {}, 'claude_cli')).toBe('sonnet')
  })

  it('偏好是清單外的裸值時顯示成「自訂」，不會變成選不到任何一項', () => {
    expect(resolveModelValue(PROVIDERS, { claude_cli: 'claude-fable-5' }, {}, 'claude_cli')).toBe(
      CUSTOM_MODEL,
    )
    expect(resolveModelDraft(PROVIDERS, { claude_cli: 'claude-fable-5' }, {}, 'claude_cli')).toBe(
      'claude-fable-5',
    )
  })

  it('這次選的優先於偏好', () => {
    expect(
      resolveModelValue(PROVIDERS, { claude_cli: 'sonnet' }, { claude_cli: 'haiku' }, 'claude_cli'),
    ).toBe('haiku')
  })

  it('**每個供應商各記一格**：切到另一個供應商會拿到它自己的偏好', () => {
    const saved = { claude_cli: 'opus', gemini: 'gemini-3.6-flash' }
    expect(resolveModelValue(PROVIDERS, saved, {}, 'claude_cli')).toBe('opus')
    expect(resolveModelValue(PROVIDERS, saved, {}, 'gemini')).toBe('gemini-3.6-flash')
  })

  it('切到沒有偏好的供應商就回到「跟隨預設」，不會沿用上一個的 model', () => {
    expect(resolveModelValue(PROVIDERS, { claude_cli: 'opus' }, {}, 'gemini')).toBe(INHERIT_MODEL)
  })
})

describe('modelRequestField', () => {
  /** 讓 store 進到「已載入供應商、選了某一個」的狀態。 */
  function pickProvider(name: string) {
    useProviderStore.getState().applyServerConfig({ default: 'claude', providers: PROVIDERS }, null)
    useProviderStore.getState().setSelected(name)
  }

  it('沒選過 model 時不帶 model 欄位（語意＝用該供應商的預設）', () => {
    pickProvider('claude_cli')
    expect(modelRequestField()).toEqual({})
    expect('model' in modelRequestField()).toBe(false)
  })

  it('選了清單裡的 model 就帶裸值進 body', () => {
    pickProvider('claude_cli')
    useProviderStore.getState().setModel('claude_cli', 'sonnet')
    expect(modelRequestField()).toEqual({ model: 'sonnet' })
  })

  it('自訂輸入送的是框裡的裸值，前後空白會去掉', () => {
    pickProvider('claude_cli')
    useProviderStore.getState().setModel('claude_cli', CUSTOM_MODEL)
    useProviderStore.getState().setModelDraft('claude_cli', '  claude-fable-5  ')
    expect(modelRequestField()).toEqual({ model: 'claude-fable-5' })
  })

  it('選了自訂但框是空的就不帶欄位（空字串不是 model 名）', () => {
    pickProvider('claude_cli')
    useProviderStore.getState().setModel('claude_cli', CUSTOM_MODEL)
    useProviderStore.getState().setModelDraft('claude_cli', '   ')
    expect(modelRequestField()).toEqual({})
  })

  it('🔴 供應商是「自動」時一律不帶 model（後端會用 400 擋這個組合）', () => {
    pickProvider('claude_cli')
    useProviderStore.getState().setModel('claude_cli', 'sonnet')
    useProviderStore.getState().setSelected(AUTO_PROVIDER)
    expect(modelRequestField()).toEqual({})

    // 連「自動」那一格底下真的記了 model」也要擋住。
    // 沒有這一段的話這則測試是假的：正常路徑本來就不會在 AUTO_PROVIDER 這個
    // 鍵底下存東西（元件在自動時是停用的），所以把 modelRequestField 裡的
    // auto 防護整條拿掉，測試照樣會綠——2026-09-17 實測確認過。
    useProviderStore.setState({ modelSelection: { [AUTO_PROVIDER]: 'sonnet' } })
    expect(modelRequestField()).toEqual({})
  })

  it('帶的是裸值，不是用量欄位那種帶前綴的識別字', () => {
    pickProvider('claude_cli')
    useProviderStore.getState().setModel('claude_cli', 'opus')
    // providers[0].model 是 'claude-cli:opus'，送出去會被後端再加一次前綴
    expect(modelRequestField()).toEqual({ model: 'opus' })
  })

  it('偏好存過就直接生效，不必每次重選', () => {
    useProviderStore
      .getState()
      .applyServerConfig({ default: 'claude', providers: PROVIDERS }, null, { gemini: 'gemini-3.6-flash' })
    useProviderStore.getState().setSelected('gemini')
    expect(modelRequestField()).toEqual({ model: 'gemini-3.6-flash' })
  })
})

describe('applyServerConfig 的 model 偏好', () => {
  it('呼叫端沒給偏好時，退回 ai.default_models', () => {
    useProviderStore
      .getState()
      .applyServerConfig({ default: 'claude', providers: PROVIDERS, default_models: { claude_cli: 'haiku' } }, null)
    expect(useProviderStore.getState().savedModels).toEqual({ claude_cli: 'haiku' })
  })

  it('/me 再刷新不會蓋掉 Viewer 當下選的 model', () => {
    useProviderStore.getState().applyServerConfig({ default: 'claude', providers: PROVIDERS }, null)
    useProviderStore.getState().setSelected('claude_cli')
    useProviderStore.getState().setModel('claude_cli', 'haiku')
    useProviderStore
      .getState()
      .applyServerConfig({ default: 'claude', providers: PROVIDERS }, null, { claude_cli: 'opus' })
    expect(modelRequestField()).toEqual({ model: 'haiku' })
  })
})

describe('loadProviders', () => {
  it('重新載入供應商清單時，不可以把已存的 model 偏好清掉', async () => {
    // `/providers` 未登入也能打，回應沒有 default_models；偏好只存在 store 裡。
    // 清掉的話畫面上看起來像「從來沒設定過」，但資料其實還在後端。
    useProviderStore.setState({ savedDefault: 'claude_cli', savedModels: { claude_cli: 'haiku' } })
    const spy = vi
      .spyOn(api, 'providers')
      .mockResolvedValue({ default: 'claude', providers: PROVIDERS })

    await useProviderStore.getState().loadProviders()

    expect(useProviderStore.getState().savedModels).toEqual({ claude_cli: 'haiku' })
    expect(useProviderStore.getState().savedDefault).toBe('claude_cli')
    spy.mockRestore()
  })

  it('回應真的帶了 default_models 就以它為準', async () => {
    useProviderStore.setState({ savedModels: { claude_cli: 'haiku' } })
    const spy = vi
      .spyOn(api, 'providers')
      .mockResolvedValue({ default: 'claude', providers: PROVIDERS, default_models: { gemini: 'gemini-3.6-flash' } })

    await useProviderStore.getState().loadProviders()

    expect(useProviderStore.getState().savedModels).toEqual({ gemini: 'gemini-3.6-flash' })
    spy.mockRestore()
  })
})

describe('providerLabel', () => {
  it('查得到就回 label', () => {
    expect(providerLabel(PROVIDERS, 'claude_cli')).toBe('Claude Code（本機 CLI）')
  })

  it('伺服器回了前端不認得的供應商時原樣顯示', () => {
    expect(providerLabel(PROVIDERS, 'future_model')).toBe('future_model')
  })
})
