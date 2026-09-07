import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { NONE_ID, useDraftStore } from './draft'
import {
  needsPublicFigureNotice,
  personaSourceLine,
  sepiaAvailability,
  settingsSummary,
  toneLabel,
  useReplySettingsStore,
} from './replySettings'
import type { Persona, PolisherInfo, Preferences, ReplyTone } from '@/lib/types'

/**
 * 回覆設定目錄 store 的單元測試。
 *
 * 為什麼這一套要存在：
 *
 * 1. **`applyPreferences` 是「重新整理後偏好還在」的唯一實作點。** 它壞掉的
 *    表現不是報錯，而是側欄顯示「跟隨預設」但實際上偏好有生效——畫面與行為
 *    不一致，而使用者只會覺得「我設過的東西不見了」。
 * 2. **`initialised` 旗標**：`/me` 可能在使用者已經改過選擇之後才回來。沒有這個
 *    保護，使用者剛選的口氣會被偏好蓋掉，而且只在網路慢的時候發生。
 * 3. **`saveDefaults` 的 null 轉換**：`PATCH /preferences` 上 `null` 是「清除偏好」，
 *    而 draft store 用 `NONE_ID`(0) 表達「這次不用」。0 直接送出去會變成
 *    「把偏好設成 id=0 的 Persona」——一個不存在的東西。
 * 4. **刪除 Persona 要連當次選擇一起清**：後端會清偏好，但本地的選擇不清的話
 *    下拉會停在一個已經不存在的 id 上，下一次產草稿會拿到 404。
 */

const TONES: ReplyTone[] = [
  { id: 'natural', label: '自然直接', description: '像平常講話', example: '例句 A' },
  { id: 'engineer', label: '工程師協作', description: '直接具體', example: '例句 B' },
]

const GITHUB_PERSONA: Persona = {
  id: 3,
  name: 'Paul Graham',
  description: 'essayist',
  source_type: 'github',
  source_repository: 'fxp/persona-distill-skills',
  source_url: null,
  source_ref: 'main',
  source_commit_sha: '24c9850e4a8bbb8b3b1ab797b428163fa3c07066',
  source_hash: 'sha256:abc',
  enabled: true,
  imported_at: '2026-09-07T00:00:00+00:00',
  refreshed_at: null,
  created_at: '2026-09-07T00:00:00+00:00',
  updated_at: '2026-09-07T00:00:00+00:00',
  profile: {
    name: 'Paul Graham',
    thinking_style: ['從第一原理拆問題'],
    communication_style: ['直接'],
    response_preferences: { verbosity: 'low' },
    avoid: ['官腔'],
  },
}

const MANUAL_PERSONA: Persona = {
  ...GITHUB_PERSONA,
  id: 4,
  name: '我的風格',
  source_type: 'manual',
  source_repository: null,
  source_ref: null,
  source_commit_sha: null,
}

const DRAFT_INITIAL = {
  toneId: null,
  personaId: null,
  customPrompt: '',
  customPromptId: null,
  sepiaEnabled: null,
}

const SETTINGS_INITIAL = {
  tones: [],
  serverDefaultTone: 'natural',
  personas: [],
  sources: [],
  replyPrompts: [],
  polishers: [],
  sepiaRules: {},
  loading: false,
  busy: false,
  error: null,
  initialised: false,
}

function prefs(overrides: Partial<Preferences> = {}): Preferences {
  return {
    pinned_space_ids: [],
    default_limit: 50,
    default_style: 'general',
    default_provider: null,
    ...overrides,
  }
}

beforeEach(() => {
  vi.restoreAllMocks()
  useDraftStore.setState({ ...DRAFT_INITIAL })
  useReplySettingsStore.setState({ ...SETTINGS_INITIAL })
})

describe('sepiaAvailability', () => {
  it('sepia 可用時回 available', () => {
    const list: PolisherInfo[] = [
      { name: 'noop', label: '不潤稿', available: true, reason: '' },
      { name: 'sepia', label: 'Sepia 潤稿', available: true, reason: '' },
    ]
    expect(sepiaAvailability(list)).toEqual({ available: true, reason: '' })
  })

  it('不可用時把原因原文帶出來（那行寫的是怎麼修）', () => {
    const list: PolisherInfo[] = [
      { name: 'sepia', label: 'Sepia 潤稿', available: false, reason: '找不到潤稿規則檔' },
    ]
    expect(sepiaAvailability(list)).toEqual({
      available: false,
      reason: '找不到潤稿規則檔',
    })
  })

  it('清單裡沒有 sepia（舊版伺服器）時回不可用，不是崩潰', () => {
    expect(sepiaAvailability([]).available).toBe(false)
    expect(sepiaAvailability([]).reason).toContain('沒有 Sepia')
  })
})

describe('toneLabel', () => {
  it('id 轉標籤', () => {
    expect(toneLabel(TONES, 'engineer')).toBe('工程師協作')
  })

  it('認不出的 id 回原字串，不在顯示路徑上壞掉', () => {
    expect(toneLabel(TONES, 'unknown_tone')).toBe('unknown_tone')
  })

  it('null 回空字串', () => {
    expect(toneLabel(TONES, null)).toBe('')
  })
})

describe('settingsSummary', () => {
  const base = {
    tones: TONES,
    toneId: null as string | null,
    personas: [GITHUB_PERSONA],
    personaId: null as number | null,
    customPrompt: '',
    customPromptId: null as number | null,
    sepiaEnabled: null as boolean | null,
  }

  it('什麼都沒選時明說「未設定」', () => {
    expect(settingsSummary(base)).toBe('未設定')
  })

  it('列出全部已選項目', () => {
    expect(
      settingsSummary({
        ...base,
        toneId: 'engineer',
        personaId: 3,
        customPrompt: '我的要求',
        sepiaEnabled: true,
      }),
    ).toBe('工程師協作 · Paul Graham · 自訂提示 · Sepia')
  })

  it('「這次不使用」（NONE_ID）不算已選，不會出現在摘要裡', () => {
    expect(settingsSummary({ ...base, personaId: NONE_ID, customPromptId: NONE_ID })).toBe(
      '未設定',
    )
  })

  it('Persona 已被刪除時不顯示殘留的 id', () => {
    expect(settingsSummary({ ...base, personaId: 999 })).toBe('未設定')
  })

  it('inline 自訂提示優先顯示（與送出時的優先序一致）', () => {
    expect(
      settingsSummary({ ...base, customPrompt: '這次的要求', customPromptId: 5 }),
    ).toBe('自訂提示')
  })

  it('只有 preset 時顯示「提示詞」', () => {
    expect(settingsSummary({ ...base, customPromptId: 5 })).toBe('提示詞')
  })

  it('sepia 關閉（false）不顯示', () => {
    expect(settingsSummary({ ...base, sepiaEnabled: false })).toBe('未設定')
  })
})

describe('personaSourceLine', () => {
  it('遠端來源顯示 repo 與短 commit（版本固定的證據要看得見）', () => {
    expect(personaSourceLine(GITHUB_PERSONA)).toBe('fxp/persona-distill-skills @ 24c9850')
  })

  it('沒有 commit 時明說未記錄，不顯示空白', () => {
    expect(personaSourceLine({ ...GITHUB_PERSONA, source_commit_sha: null })).toContain(
      '未記錄',
    )
  })

  it('自訂 Persona 不談來源', () => {
    expect(personaSourceLine(MANUAL_PERSONA)).toBe('自訂 Persona')
  })
})

describe('needsPublicFigureNotice', () => {
  it('公開人物來源要顯示免責提示', () => {
    expect(needsPublicFigureNotice(GITHUB_PERSONA)).toBe(true)
  })

  it('自訂 Persona 不需要', () => {
    expect(needsPublicFigureNotice(MANUAL_PERSONA)).toBe(false)
  })

  it('沒選 Persona 時不需要', () => {
    expect(needsPublicFigureNotice(undefined)).toBe(false)
  })
})

describe('applyPreferences', () => {
  it('把偏好套成 draft store 的初始選擇', () => {
    useReplySettingsStore.getState().applyPreferences(
      prefs({
        default_reply_tone: 'engineer',
        default_persona_id: 3,
        default_reply_prompt_id: 5,
        default_sepia_enabled: true,
      }),
    )
    const draft = useDraftStore.getState()
    expect(draft.toneId).toBe('engineer')
    expect(draft.personaId).toBe(3)
    expect(draft.customPromptId).toBe(5)
    expect(draft.sepiaEnabled).toBe(true)
  })

  it('sepia 偏好是明確的 false 時也要套用（不是只有 true 才算）', () => {
    useReplySettingsStore.getState().applyPreferences(prefs({ default_sepia_enabled: false }))
    expect(useDraftStore.getState().sepiaEnabled).toBe(false)
  })

  it('沒有偏好的欄位保持 null（＝跟隨預設）', () => {
    useReplySettingsStore.getState().applyPreferences(prefs({ default_reply_tone: 'soft' }))
    const draft = useDraftStore.getState()
    expect(draft.toneId).toBe('soft')
    expect(draft.personaId).toBeNull()
    expect(draft.sepiaEnabled).toBeNull()
  })

  it('只在第一次套用——之後不覆寫使用者當下的選擇', () => {
    useReplySettingsStore.getState().applyPreferences(prefs({ default_reply_tone: 'engineer' }))
    useDraftStore.getState().setToneId('concise')
    // /me 又回來一次（例如重新整理後的第二次請求）
    useReplySettingsStore.getState().applyPreferences(prefs({ default_reply_tone: 'engineer' }))
    expect(useDraftStore.getState().toneId).toBe('concise')
  })

  it('preferences 為 null／undefined 時什麼都不做', () => {
    useReplySettingsStore.getState().applyPreferences(null)
    useReplySettingsStore.getState().applyPreferences(undefined)
    expect(useDraftStore.getState().toneId).toBeNull()
    expect(useReplySettingsStore.getState().initialised).toBe(false)
  })
})

describe('saveDefaults', () => {
  it('把當下選擇送去 PATCH /preferences', async () => {
    const spy = vi.spyOn(api, 'updatePreferences').mockResolvedValue(prefs())
    const draft = useDraftStore.getState()
    draft.setToneId('engineer')
    draft.setPersonaId(3)
    draft.setCustomPromptId(5)
    draft.setSepiaEnabled(true)

    await expect(useReplySettingsStore.getState().saveDefaults()).resolves.toBe(true)
    expect(spy).toHaveBeenCalledWith({
      default_reply_tone: 'engineer',
      default_persona_id: 3,
      default_reply_prompt_id: 5,
      default_sepia_enabled: true,
    })
  })

  it('NONE_ID（0）要轉成 null——送 0 會變成「把偏好設成不存在的 id」', async () => {
    const spy = vi.spyOn(api, 'updatePreferences').mockResolvedValue(prefs())
    useDraftStore.getState().setPersonaId(NONE_ID)
    useDraftStore.getState().setCustomPromptId(NONE_ID)

    await useReplySettingsStore.getState().saveDefaults()
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ default_persona_id: null, default_reply_prompt_id: null }),
    )
  })

  it('sepia 的明確 false 要照原樣送（不可以被轉成 null）', async () => {
    const spy = vi.spyOn(api, 'updatePreferences').mockResolvedValue(prefs())
    useDraftStore.getState().setSepiaEnabled(false)
    await useReplySettingsStore.getState().saveDefaults()
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ default_sepia_enabled: false }),
    )
  })

  it('失敗時回 false 並把訊息寫進 error，不 throw', async () => {
    vi.spyOn(api, 'updatePreferences').mockRejectedValue(new Error('網路壞了'))
    await expect(useReplySettingsStore.getState().saveDefaults()).resolves.toBe(false)
    expect(useReplySettingsStore.getState().error).toBe('網路壞了')
  })
})

describe('deletePersona', () => {
  it('刪掉的正好是當次選擇時，連選擇一起清（否則下拉停在不存在的 id）', async () => {
    vi.spyOn(api, 'deletePersona').mockResolvedValue({ deleted: true })
    useReplySettingsStore.setState({ personas: [GITHUB_PERSONA] })
    useDraftStore.getState().setPersonaId(3)

    await expect(useReplySettingsStore.getState().deletePersona(3)).resolves.toBe(true)
    expect(useDraftStore.getState().personaId).toBeNull()
    expect(useReplySettingsStore.getState().personas).toEqual([])
  })

  it('刪掉別的 Persona 時不動當次選擇', async () => {
    vi.spyOn(api, 'deletePersona').mockResolvedValue({ deleted: true })
    useReplySettingsStore.setState({ personas: [GITHUB_PERSONA, MANUAL_PERSONA] })
    useDraftStore.getState().setPersonaId(3)

    await useReplySettingsStore.getState().deletePersona(4)
    expect(useDraftStore.getState().personaId).toBe(3)
  })

  it('失敗時回 false 且清單不變', async () => {
    vi.spyOn(api, 'deletePersona').mockRejectedValue(new Error('刪不掉'))
    useReplySettingsStore.setState({ personas: [GITHUB_PERSONA] })
    await expect(useReplySettingsStore.getState().deletePersona(3)).resolves.toBe(false)
    expect(useReplySettingsStore.getState().personas).toHaveLength(1)
  })
})

describe('deleteReplyPrompt', () => {
  it('刪掉的正好是當次選擇時，連選擇一起清', async () => {
    vi.spyOn(api, 'deleteReplyPrompt').mockResolvedValue({ deleted: true })
    useReplySettingsStore.setState({
      replyPrompts: [
        {
          id: 5,
          name: 'x',
          description: '',
          prompt: 'p',
          created_at: '',
          updated_at: '',
        },
      ],
    })
    useDraftStore.getState().setCustomPromptId(5)

    await useReplySettingsStore.getState().deleteReplyPrompt(5)
    expect(useDraftStore.getState().customPromptId).toBeNull()
  })
})

describe('load', () => {
  it('單一端點失敗不會讓其他三個的結果消失', async () => {
    vi.spyOn(api, 'replyTones').mockResolvedValue({ tones: TONES, default: 'natural' })
    vi.spyOn(api, 'personas').mockRejectedValue(new Error('壞了'))
    vi.spyOn(api, 'replyPrompts').mockResolvedValue({ reply_prompts: [] })
    vi.spyOn(api, 'polishers').mockResolvedValue({
      polishers: [{ name: 'sepia', label: 'Sepia 潤稿', available: true, reason: '' }],
      sepia: { version: '0.8.0' },
    })

    await useReplySettingsStore.getState().load()
    const state = useReplySettingsStore.getState()
    expect(state.tones).toHaveLength(2)
    expect(state.personas).toEqual([])
    expect(state.sepiaRules.version).toBe('0.8.0')
    expect(state.loading).toBe(false)
  })

  it('重入時直接返回，不重複打 API', async () => {
    const spy = vi.spyOn(api, 'replyTones').mockResolvedValue({ tones: [], default: 'natural' })
    useReplySettingsStore.setState({ loading: true })
    await useReplySettingsStore.getState().load()
    expect(spy).not.toHaveBeenCalled()
  })
})
