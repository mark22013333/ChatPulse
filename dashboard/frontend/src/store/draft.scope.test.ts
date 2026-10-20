import { beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '@/lib/api'
import * as sse from '@/lib/sse'
import { useDraftStore } from './draft'

function deferred() {
  let resolve!: () => void
  let reject!: (error: Error) => void
  const promise = new Promise<void>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.restoreAllMocks()
  useDraftStore.getState().reset()
  vi.spyOn(api, 'storedDraft').mockRejectedValue(new ApiError(404, 'DRAFT_NOT_FOUND', '無草稿'))
})

it('A 未產生即換 B，B request 不沿用來源，保留偏好', async () => {
  const stream = vi.spyOn(sse, 'streamSse').mockResolvedValue()
  const store = useDraftStore.getState()
  store.activateMention(7)
  store.toggleReference('spaces/fake')
  store.toggleCodeRef(3, 'production')
  store.setCodeTerms('known_symbol')
  store.setReferenceSearch('合成')
  store.setToneId('engineer')
  store.setCustomPrompt('只談已知資訊')
  await store.generate(8)
  expect(stream.mock.calls[0][1]).toMatchObject({ reference_space_ids: [], code_refs: [], tone_id: 'engineer', custom_prompt: '只談已知資訊' })
  expect(stream.mock.calls[0][1]).not.toHaveProperty('code_terms')
})

it('A 晚到所有 callback、catch 和 finally 不覆蓋 B', async () => {
  const a = deferred()
  const b = deferred()
  const handlers: sse.SseHandlers[] = []
  vi.spyOn(sse, 'streamSse').mockImplementation((_url, _body, callbacks) => {
    handlers.push(callbacks)
    return handlers.length === 1 ? a.promise : b.promise
  })
  const first = useDraftStore.getState().generate(7)
  const second = useDraftStore.getState().generate(8)
  handlers[1].onChunk?.({ type: 'chunk', text: 'B' })
  handlers[0].onChunk?.({ type: 'chunk', text: 'A' })
  handlers[0].onDone?.({ type: 'done', draft_id: 99, reply: 'A' })
  handlers[0].onError?.({ type: 'error', code: 'INTERNAL_ERROR', message: 'A' })
  a.reject(new Error('A'))
  await first
  expect(useDraftStore.getState()).toMatchObject({ raw: 'B', draftId: null, error: null, streaming: true })
  b.resolve()
  await second
})

it('A stored 回應晚到 B 或同題 generate 後都不能落地', async () => {
  let restore!: (value: Awaited<ReturnType<typeof api.storedDraft>>) => void
  vi.mocked(api.storedDraft).mockImplementationOnce(() => new Promise((resolve) => { restore = resolve }))
  useDraftStore.getState().activateMention(7)
  const stored = { draft_id: 70, mention_id: 7, content_md: '舊 A', generation_config: {}, created_at: '2026-01-01T00:00:00Z', sent_at: null }
  useDraftStore.getState().activateMention(8)
  restore(stored)
  await Promise.resolve()
  expect(useDraftStore.getState()).toMatchObject({ raw: '', draftId: null, restored: false })

  vi.mocked(api.storedDraft).mockImplementationOnce(() => new Promise((resolve) => { restore = resolve }))
  const pending = deferred()
  let callbacks: sse.SseHandlers = {}
  vi.spyOn(sse, 'streamSse').mockImplementation((_url, _body, handlers) => { callbacks = handlers; return pending.promise })
  useDraftStore.getState().activateMention(7)
  const run = useDraftStore.getState().generate(7)
  callbacks.onChunk?.({ type: 'chunk', text: '新 A' })
  restore(stored)
  await Promise.resolve()
  expect(useDraftStore.getState()).toMatchObject({ raw: '新 A', draftId: null, restored: false, streaming: true })
  pending.resolve()
  await run
})

it('同題第二輪取代第一輪，done 後任何 callback 不再更動內容', async () => {
  const first = deferred()
  const second = deferred()
  const callbacks: sse.SseHandlers[] = []
  vi.spyOn(sse, 'streamSse').mockImplementation((_url, _body, handlers) => {
    callbacks.push(handlers)
    return callbacks.length === 1 ? first.promise : second.promise
  })
  const one = useDraftStore.getState().generate(7)
  const two = useDraftStore.getState().generate(7)
  callbacks[1].onMeta?.({ type: 'meta', provider: 'fake' })
  callbacks[1].onChunk?.({ type: 'chunk', text: '### ✍️ 建議回話\n第二輪' })
  callbacks[0].onMeta?.({ type: 'meta', provider: 'old' })
  callbacks[0].onDone?.({ type: 'done', draft_id: 1, reply: '第一輪' })
  first.resolve()
  await one
  expect(useDraftStore.getState().streaming).toBe(true)
  callbacks[1].onDone?.({ type: 'done', draft_id: 2, reply: '完成版本' })
  callbacks[1].onChunk?.({ type: 'chunk', text: '晚到文字' })
  callbacks[1].onError?.({ type: 'error', code: 'INTERNAL_ERROR', message: '晚到錯誤' })
  second.reject(new Error('晚到 catch'))
  await two
  expect(useDraftStore.getState()).toMatchObject({ replyText: '完成版本', draftId: 2, error: null, meta: { provider: 'fake' } })
})

it('abort 撤銷操作但保留當題來源與已到文字，同題啟用不取消串流', async () => {
  const pending = deferred()
  let callbacks: sse.SseHandlers = {}
  let signal: AbortSignal | undefined
  vi.spyOn(sse, 'streamSse').mockImplementation((_url, _body, handlers, runSignal) => { callbacks = handlers; signal = runSignal; return pending.promise })
  useDraftStore.getState().activateMention(7)
  const epoch = useDraftStore.getState().active?.epoch
  useDraftStore.getState().toggleReference('spaces/fake')
  const run = useDraftStore.getState().generate(7)
  useDraftStore.getState().activateMention(7)
  expect(signal?.aborted).toBe(false)
  expect(useDraftStore.getState().active?.epoch).toBe(epoch)
  callbacks.onChunk?.({ type: 'chunk', text: '部分內容' })
  useDraftStore.getState().abort()
  callbacks.onChunk?.({ type: 'chunk', text: '不應出現' })
  callbacks.onDone?.({ type: 'done', draft_id: 9, reply: '不應出現' })
  pending.resolve()
  await run
  expect(useDraftStore.getState()).toMatchObject({ raw: '部分內容', draftId: null, streaming: false, active: { referenceSpaceIds: ['spaces/fake'] } })
})

it('還原錯誤只忽略 DRAFT_NOT_FOUND，歷史 meta 不回填本次來源', async () => {
  vi.mocked(api.storedDraft).mockRejectedValueOnce(new ApiError(404, 'MENTION_NOT_FOUND', '不存在'))
  await useDraftStore.getState().loadStored(7)
  expect(useDraftStore.getState().error).toBeTruthy()
  vi.mocked(api.storedDraft).mockResolvedValueOnce({ draft_id: 70, mention_id: 8, content_md: '歷史草稿', generation_config: { meta: { type: 'meta', reference_spaces: [{ space_id: 'spaces/old', space_name: '歷史', message_count: 1 }] } }, created_at: '2026-01-01T00:00:00Z', sent_at: null })
  await useDraftStore.getState().loadStored(8)
  expect(useDraftStore.getState()).toMatchObject({ active: { referenceSpaceIds: [], codeRefs: [], codeTerms: '' }, meta: { reference_spaces: [{ space_id: 'spaces/old' }] } })
})

it.each([true, false])('送出中切 B，A 的成功或錯誤不更動 B，成功=%s', async (success) => {
  let resolve!: (value: Awaited<ReturnType<typeof api.sendReply>>) => void
  let reject!: (error: Error) => void
  const sent = vi.spyOn(api, 'sendReply').mockImplementation(() => new Promise((yes, no) => { resolve = yes; reject = no }))
  useDraftStore.getState().activateMention(7)
  useDraftStore.setState({ replyText: '送出 A', draftId: 70, meta: { type: 'meta', answering: [{ mention_id: 7 }, { mention_id: 9 }] } })
  const epoch = useDraftStore.getState().active?.epoch
  const result = useDraftStore.getState().send(7).catch((error: unknown) => error)
  useDraftStore.getState().activateMention(8)
  useDraftStore.setState({ replyText: 'B', error: null })
  const fakeMention = { id: 7, state: 'resolved' }
  if (success) resolve({ mentions: [fakeMention] } as never)
  else reject(new Error('A 送出失敗'))
  const receipt = await result
  expect(sent).toHaveBeenCalledWith(7, { text: '送出 A', draft_id: 70, merge_mention_ids: [9] })
  expect(useDraftStore.getState()).toMatchObject({ replyText: 'B', error: null, sendingMentionIds: [] })
  if (success) expect(receipt).toEqual({ mentions: [fakeMention], scopeEpoch: epoch })
})

it('送出 A→B→A 仍防止重送，錯誤 ID 不發送', async () => {
  let resolve!: (value: Awaited<ReturnType<typeof api.sendReply>>) => void
  const sent = vi.spyOn(api, 'sendReply').mockImplementation(() => new Promise((yes) => { resolve = yes }))
  useDraftStore.getState().activateMention(7)
  useDraftStore.getState().setReplyText('A')
  const first = useDraftStore.getState().send(7)
  useDraftStore.getState().activateMention(8)
  await expect(useDraftStore.getState().send(7)).rejects.toBeInstanceOf(Error)
  useDraftStore.getState().activateMention(7)
  useDraftStore.getState().setReplyText('新 A')
  await expect(useDraftStore.getState().send(7)).rejects.toBeInstanceOf(Error)
  expect(sent).toHaveBeenCalledTimes(1)
  resolve({ mentions: [] } as never)
  await first
  expect(useDraftStore.getState().replyText).toBe('新 A')
})

it.each(['reference', 'code', 'terms'])('legacy 草稿手動編輯後更改 %s 保留文字與草稿 ID', async (change) => {
  let callbacks: sse.SseHandlers = {}
  vi.spyOn(sse, 'streamSse').mockImplementation(async (_url, _body, handlers) => {
    callbacks = handlers
    callbacks.onChunk?.({ type: 'chunk', text: '### ✍️ 建議回話\n產生的回話' })
    callbacks.onDone?.({ type: 'done', draft_id: 70, reply: '產生的回話' })
  })
  const store = useDraftStore.getState()
  await store.generate(7)
  store.setReplyText('Viewer 手動編輯的回話')
  if (change === 'reference') store.toggleReference('spaces/reference')
  if (change === 'code') store.toggleCodeRef(3, 'production')
  if (change === 'terms') store.setCodeTerms('known_symbol')
  expect(useDraftStore.getState().replyText).toBe('Viewer 手動編輯的回話')
  expect(useDraftStore.getState().draftId).toBe(70)
  expect(useDraftStore.getState().replyEdited).toBe(true)
  expect(useDraftStore.getState().active).toMatchObject({
    referenceSpaceIds: change === 'reference' ? ['spaces/reference'] : [],
    codeRefs: change === 'code' ? [{ project_id: 3, environment: 'production' }] : [],
    codeTerms: change === 'terms' ? 'known_symbol' : '',
  })
})

it.each(['reference', 'code', 'terms'])('legacy 串流手動編輯後更改 %s 保留操作與後續結果', async (change) => {
  const pending = deferred()
  let callbacks: sse.SseHandlers = {}
  let signal: AbortSignal | undefined
  vi.spyOn(sse, 'streamSse').mockImplementation((_url, _body, handlers, runSignal) => {
    callbacks = handlers
    signal = runSignal
    return pending.promise
  })
  const store = useDraftStore.getState()
  const run = store.generate(7)
  callbacks.onChunk?.({ type: 'chunk', text: '### ✍️ 建議回話\n部分回話' })
  store.setReplyText('Viewer 串流中編輯的回話')
  if (change === 'reference') store.toggleReference('spaces/reference')
  if (change === 'code') store.toggleCodeRef(3, 'production')
  if (change === 'terms') store.setCodeTerms('known_symbol')
  expect(useDraftStore.getState().replyText).toBe('Viewer 串流中編輯的回話')
  expect(signal?.aborted).toBe(false)
  callbacks.onChunk?.({ type: 'chunk', text: '後續內容' })
  callbacks.onDone?.({ type: 'done', draft_id: 71, reply: 'AI 完整回話' })
  pending.resolve()
  await run
  expect(useDraftStore.getState()).toMatchObject({
    raw: '### ✍️ 建議回話\n部分回話後續內容',
    replyText: 'Viewer 串流中編輯的回話', replyEdited: true, draftId: 71, streaming: false,
  })
})
