import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, LIMIT_DEFAULT } from '@/lib/api'
import * as sse from '@/lib/sse'
import { NONE_ID, splitDraft, useDraftStore } from './draft'
import { useProviderStore } from './providers'
import type { SseDone, SseMeta } from '@/lib/types'

/**
 * Draft store 的單元測試，重點在**送出去的 request body** 與**回覆設定的生命週期**。
 *
 * 為什麼這一套要存在：
 *
 * 1. 回覆設定（口氣／Persona／自訂提示／Sepia）刻意**不在 `reset()` 裡被清空**，
 *    因為它們是跨 Mention 的偏好。如果哪天有人「順手」把它們加進 reset 的清單，
 *    使用者切一則 Mention 就會被洗掉設定——而畫面上看起來完全正常，
 *    只是每次都要重選一次。
 * 2. `done.reply` 帶的是潤稿後的內容。**不採用它就等於 Sepia 完全沒生效**：
 *    畫面會是未潤稿的版本、資料庫是潤稿後的版本，而使用者按送出時送的是
 *    畫面那一份。這個錯誤從畫面上看不出來。
 * 3. 回覆設定一律「有值才送」。送 `null` 在後端是「清除偏好」的意思，
 *    混用會讓使用者的 Viewer 偏好被一次草稿意外清掉。
 */

const INITIAL = {
  referenceSpaceIds: [],
  codeRefs: [],
  codeTerms: '',
  referenceSearch: '',
  refLimit: LIMIT_DEFAULT,
  refLimitError: null,
  toneId: null,
  personaId: null,
  customPrompt: '',
  customPromptId: null,
  sepiaEnabled: null,
  streaming: false,
  raw: '',
  meta: null,
  draftId: null,
  error: null,
  mentionId: null,
  polish: null,
  replyText: '',
  replyEdited: false,
  sending: false,
}

/** 攔下 streamSse 的呼叫，回傳送出去的 body 與可以手動觸發的 handlers。 */
function captureStream() {
  const captured: { body: unknown; handlers: sse.SseHandlers } = {
    body: null,
    handlers: {},
  }
  vi.spyOn(sse, 'streamSse').mockImplementation(async (_url, body, handlers) => {
    captured.body = body
    captured.handlers = handlers
  })
  return captured
}

beforeEach(() => {
  vi.restoreAllMocks()
  useDraftStore.setState({ ...INITIAL })
  useProviderStore.setState({
    providers: [],
    serverDefault: null,
    savedDefault: null,
    selected: '__auto__',
    initialised: false,
    loading: false,
    saving: false,
    error: null,
  })
})

describe('generate 的 request body', () => {
  it('什麼都沒選時不送任何回覆設定欄位（舊 client 的形狀）', async () => {
    const captured = captureStream()
    await useDraftStore.getState().generate(7)
    const body = captured.body as Record<string, unknown>
    expect(Object.keys(body).sort()).toEqual(
      ['code_refs', 'limit', 'merge_mention_ids', 'reference_space_ids'].sort(),
    )
  })

  it('選了口氣就送 tone_id', async () => {
    const captured = captureStream()
    useDraftStore.getState().setToneId('engineer')
    await useDraftStore.getState().generate(7)
    expect((captured.body as Record<string, unknown>).tone_id).toBe('engineer')
  })

  it('選了 Persona 就送 persona_id', async () => {
    const captured = captureStream()
    useDraftStore.getState().setPersonaId(3)
    await useDraftStore.getState().generate(7)
    expect((captured.body as Record<string, unknown>).persona_id).toBe(3)
  })

  it('「這次不使用 Persona」送 0，不是省略——省略會被當成沿用偏好', async () => {
    const captured = captureStream()
    useDraftStore.getState().setPersonaId(NONE_ID)
    await useDraftStore.getState().generate(7)
    expect((captured.body as Record<string, unknown>).persona_id).toBe(0)
  })

  it('清除 Persona 選擇（null）時整個欄位不出現', async () => {
    const captured = captureStream()
    useDraftStore.getState().setPersonaId(3)
    useDraftStore.getState().setPersonaId(null)
    await useDraftStore.getState().generate(7)
    expect('persona_id' in (captured.body as Record<string, unknown>)).toBe(false)
  })

  it('自訂提示會 trim 後送出', async () => {
    const captured = captureStream()
    useDraftStore.getState().setCustomPrompt('  直接說卡在哪  ')
    await useDraftStore.getState().generate(7)
    expect((captured.body as Record<string, unknown>).custom_prompt).toBe('直接說卡在哪')
  })

  it('只有空白的自訂提示視為沒填，欄位不出現', async () => {
    const captured = captureStream()
    useDraftStore.getState().setCustomPrompt('   \n  ')
    await useDraftStore.getState().generate(7)
    expect('custom_prompt' in (captured.body as Record<string, unknown>)).toBe(false)
  })

  it('inline 自訂提示優先於 preset id（兩者不會同時送）', async () => {
    const captured = captureStream()
    useDraftStore.getState().setCustomPromptId(5)
    useDraftStore.getState().setCustomPrompt('這次特別的要求')
    await useDraftStore.getState().generate(7)
    const body = captured.body as Record<string, unknown>
    expect(body.custom_prompt).toBe('這次特別的要求')
    expect('custom_prompt_id' in body).toBe(false)
  })

  it('沒有 inline 內容時才送 preset id', async () => {
    const captured = captureStream()
    useDraftStore.getState().setCustomPromptId(5)
    await useDraftStore.getState().generate(7)
    expect((captured.body as Record<string, unknown>).custom_prompt_id).toBe(5)
  })

  it('Sepia 開關 true／false 都會送，null 才省略', async () => {
    const captured = captureStream()
    useDraftStore.getState().setSepiaEnabled(true)
    await useDraftStore.getState().generate(7)
    expect((captured.body as Record<string, unknown>).sepia_enabled).toBe(true)

    useDraftStore.getState().setSepiaEnabled(false)
    await useDraftStore.getState().generate(7)
    expect((captured.body as Record<string, unknown>).sepia_enabled).toBe(false)

    useDraftStore.getState().setSepiaEnabled(null)
    await useDraftStore.getState().generate(7)
    expect('sepia_enabled' in (captured.body as Record<string, unknown>)).toBe(false)
  })

  it('既有欄位（參考來源、抓取則數、合併）不受影響', async () => {
    const captured = captureStream()
    useDraftStore.getState().toggleReference('spaces/AAA')
    useDraftStore.getState().toggleCodeRef(2, 'production')
    useDraftStore.getState().setRefLimit('120')
    useDraftStore.getState().setToneId('concise')
    await useDraftStore.getState().generate(7, [8, 9, 7])
    const body = captured.body as Record<string, unknown>
    expect(body.reference_space_ids).toEqual(['spaces/AAA'])
    expect(body.code_refs).toEqual([{ project_id: 2, environment: 'production' }])
    expect(body.limit).toBe(120)
    // 自己不可以出現在 merge 清單裡
    expect(body.merge_mention_ids).toEqual([8, 9])
  })

  it('**沒填檢索關鍵字時整個 code_terms 欄位不出現**', async () => {
    // 空陣列與省略在後端是同一件事，送空的只是噪音。這條同時守著上面那條
    // 「什麼都沒選時的 key 清單」不會因為加了新欄位而變長。
    const captured = captureStream()
    useDraftStore.getState().toggleCodeRef(2, 'production')
    await useDraftStore.getState().generate(7)
    expect('code_terms' in (captured.body as Record<string, unknown>)).toBe(false)
  })

  it('**填了就送 code_terms（完全取代後端的自動抽詞）**', async () => {
    const captured = captureStream()
    useDraftStore.getState().toggleCodeRef(2, 'production')
    useDraftStore.getState().setCodeTerms('sendPush, retryCount')
    await useDraftStore.getState().generate(7)
    expect((captured.body as Record<string, unknown>).code_terms).toEqual([
      'sendPush',
      'retryCount',
    ])
  })

  it('只有空白／逗號的關鍵字視為沒填，欄位不出現', async () => {
    const captured = captureStream()
    useDraftStore.getState().setCodeTerms('  ,  ，  ')
    await useDraftStore.getState().generate(7)
    expect('code_terms' in (captured.body as Record<string, unknown>)).toBe(false)
  })

  it('抓取則數不合法時不發請求（既有行為，不可被回覆設定破壞）', async () => {
    const captured = captureStream()
    useDraftStore.getState().setRefLimit('abc')
    useDraftStore.getState().setToneId('engineer')
    await useDraftStore.getState().generate(7)
    expect(captured.body).toBeNull()
  })
})

describe('done 事件的潤稿結果', () => {
  const META: SseMeta = { type: 'meta', reply: { sepia: true } }

  async function runWithDone(done: SseDone, raw: string) {
    const captured = captureStream()
    useDraftStore.getState().setSepiaEnabled(true)
    await useDraftStore.getState().generate(7)
    captured.handlers.onMeta?.(META)
    captured.handlers.onChunk?.({ type: 'chunk', text: raw })
    captured.handlers.onDone?.(done)
  }

  it('採用 done.reply 當作編輯器內容（否則 Sepia 等於沒生效）', async () => {
    await runWithDone(
      { type: 'done', draft_id: 1, reply: '潤稿後的版本', polish: { polisher: 'sepia', polished: true } },
      '### ✍️ 建議回話\n未潤稿的版本',
    )
    expect(useDraftStore.getState().replyText).toBe('潤稿後的版本')
  })

  it('沒有 done.reply 時退回從 raw 切出來的建議回話', async () => {
    await runWithDone({ type: 'done', draft_id: 1 }, '### ✍️ 建議回話\n未潤稿的版本')
    expect(useDraftStore.getState().replyText).toBe('未潤稿的版本')
  })

  it('done.reply 為 null（潤稿被退回）時也退回 raw 的內容', async () => {
    await runWithDone(
      {
        type: 'done',
        draft_id: 1,
        reply: null,
        polish: { polisher: 'sepia', polished: false, fallback_reason: '數字被改了' },
      },
      '### ✍️ 建議回話\n未潤稿的版本',
    )
    expect(useDraftStore.getState().replyText).toBe('未潤稿的版本')
  })

  it('使用者已經編輯過時，潤稿結果不覆寫他的版本', async () => {
    const captured = captureStream()
    await useDraftStore.getState().generate(7)
    captured.handlers.onChunk?.({ type: 'chunk', text: '### ✍️ 建議回話\n串流的版本' })
    useDraftStore.getState().setReplyText('我自己改的版本')
    captured.handlers.onDone?.({ type: 'done', draft_id: 1, reply: '潤稿後的版本' })
    expect(useDraftStore.getState().replyText).toBe('我自己改的版本')
  })

  it('polish meta 會存進 store 供 UI 顯示', async () => {
    await runWithDone(
      {
        type: 'done',
        draft_id: 1,
        reply: null,
        polish: { polisher: 'sepia', polished: false, fallback_reason: '遺失了原文的數字：30' },
      },
      '### ✍️ 建議回話\n內容',
    )
    const polish = useDraftStore.getState().polish
    expect(polish?.polished).toBe(false)
    expect(polish?.fallback_reason).toContain('30')
  })

  it('沒開潤稿時 polish 保持 null', async () => {
    const captured = captureStream()
    await useDraftStore.getState().generate(7)
    captured.handlers.onDone?.({ type: 'done', draft_id: 1 })
    expect(useDraftStore.getState().polish).toBeNull()
  })
})

describe('reset 不可以清掉跨 Mention 的偏好', () => {
  it('保留回覆設定（切一則 Mention 不該洗掉選好的口氣）', () => {
    const store = useDraftStore.getState()
    store.setToneId('engineer')
    store.setPersonaId(3)
    store.setCustomPrompt('我的要求')
    store.setCustomPromptId(5)
    store.setSepiaEnabled(true)

    useDraftStore.getState().reset()

    const after = useDraftStore.getState()
    expect(after.toneId).toBe('engineer')
    expect(after.personaId).toBe(3)
    expect(after.customPrompt).toBe('我的要求')
    expect(after.customPromptId).toBe(5)
    expect(after.sepiaEnabled).toBe(true)
  })

  it('保留參考來源與抓取則數（既有行為）', () => {
    const store = useDraftStore.getState()
    store.toggleReference('spaces/AAA')
    store.toggleCodeRef(2, 'uat')
    store.setRefLimit('77')
    store.setReferenceSearch('關鍵字')

    useDraftStore.getState().reset()

    const after = useDraftStore.getState()
    expect(after.referenceSpaceIds).toEqual(['spaces/AAA'])
    expect(after.codeRefs).toEqual([{ project_id: 2, environment: 'uat' }])
    expect(after.refLimit).toBe(77)
    expect(after.referenceSearch).toBe('關鍵字')
  })

  it('**保留手動指定的檢索關鍵字**（與參考專案同一類的跨 Mention 偏好）', () => {
    // 切一則 Mention 就把它洗掉的話，使用者每次都得重打一次——而參考專案
    // 的勾選還留著，所以他不會想到關鍵字被清了，只會覺得搜出來的結果變了
    useDraftStore.getState().setCodeTerms('sendPush retryCount')

    useDraftStore.getState().reset()

    expect(useDraftStore.getState().codeTerms).toBe('sendPush retryCount')
  })

  it('清掉這一次草稿的結果（含 polish）', () => {
    useDraftStore.setState({
      raw: '內容',
      draftId: 9,
      mentionId: 7,
      replyText: '回話',
      replyEdited: true,
      polish: { polisher: 'sepia', polished: true },
      error: '錯誤',
    })

    useDraftStore.getState().reset()

    const after = useDraftStore.getState()
    expect(after.raw).toBe('')
    expect(after.draftId).toBeNull()
    expect(after.mentionId).toBeNull()
    expect(after.replyText).toBe('')
    expect(after.replyEdited).toBe(false)
    expect(after.polish).toBeNull()
    expect(after.error).toBeNull()
  })
})

describe('generate 會重置這一次的結果但保留設定', () => {
  it('重新產生時清掉上一次的 polish 結果', async () => {
    captureStream()
    useDraftStore.setState({ polish: { polisher: 'sepia', polished: true } })
    await useDraftStore.getState().generate(7)
    expect(useDraftStore.getState().polish).toBeNull()
  })

  it('重新產生時不清掉回覆設定', async () => {
    captureStream()
    useDraftStore.getState().setToneId('soft')
    useDraftStore.getState().setSepiaEnabled(true)
    await useDraftStore.getState().generate(7)
    expect(useDraftStore.getState().toneId).toBe('soft')
    expect(useDraftStore.getState().sepiaEnabled).toBe(true)
  })

  it('mentionId 會換成新的那一則（切換 Mention 的歸屬標記）', async () => {
    captureStream()
    await useDraftStore.getState().generate(7)
    expect(useDraftStore.getState().mentionId).toBe(7)
    await useDraftStore.getState().generate(9)
    expect(useDraftStore.getState().mentionId).toBe(9)
  })
})

describe('splitDraft 仍然照契約切段', () => {
  it('切出建議回話', () => {
    const s = splitDraft('### 🧭 脈絡分析\n背景\n\n### ✍️ 建議回話\n回話內容')
    expect(s.replyStarted).toBe(true)
    expect(s.reply).toBe('回話內容')
    // context 保留標題後的原始換行（既有行為，Markdown 元件會處理）
    expect(s.context.trim()).toBe('背景')
  })

  it('標題還沒串流出來時 replyStarted 為 false', () => {
    const s = splitDraft('### 🧭 脈絡分析\n背景還在寫')
    expect(s.replyStarted).toBe(false)
    expect(s.reply).toBe('')
  })
})

describe('send 用 meta.answering 決定要結掉哪幾則', () => {
  it('不沿用送出前的勾選（伺服器實際採用的才算數）', async () => {
    const spy = vi.spyOn(api, 'sendReply').mockResolvedValue({
      status: 'ok',
      message_id: 'm1',
      createTime: '2026-09-07T00:00:00Z',
      mention: null,
      mentions: [],
    } as never)
    useDraftStore.setState({
      replyText: '回話',
      draftId: 3,
      meta: {
        type: 'meta',
        answering: [{ mention_id: 7 }, { mention_id: 8 }],
      } as SseMeta,
    })
    await useDraftStore.getState().send(7)
    expect(spy).toHaveBeenCalledWith(7, {
      text: '回話',
      draft_id: 3,
      merge_mention_ids: [8],
    })
  })
})
