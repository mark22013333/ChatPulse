import { beforeEach, describe, expect, it } from 'vitest'
import {
  AUTO_PROVIDER,
  providerLabel,
  providerRequestField,
  resolveSelected,
  useProviderStore,
} from './providers'
import type { AIProvider } from '@/lib/types'

const PROVIDERS: AIProvider[] = [
  { name: 'claude_cli', label: 'Claude Code（本機 CLI）', model: 'claude-cli:opus', available: true, reason: '使用本機 claude' },
  { name: 'gemini', label: 'Gemini（Google AI Studio）', model: 'gemini-3.6-flash', available: true, reason: '使用 GOOGLE_API_KEY' },
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
    initialised: false,
    loading: false,
    saving: false,
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

describe('providerLabel', () => {
  it('查得到就回 label', () => {
    expect(providerLabel(PROVIDERS, 'claude_cli')).toBe('Claude Code（本機 CLI）')
  })

  it('伺服器回了前端不認得的供應商時原樣顯示', () => {
    expect(providerLabel(PROVIDERS, 'future_model')).toBe('future_model')
  })
})
