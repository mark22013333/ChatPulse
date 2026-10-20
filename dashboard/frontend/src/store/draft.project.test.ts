import { beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '@/lib/api'
import * as sse from '@/lib/sse'
import type { InquiryScope, Me, ProjectProfile } from '@/lib/types'
import { useAuthStore } from './auth'
import { useDraftStore } from './draft'

const profile: ProjectProfile = { id: 1, name: '甲案', revision: 3, sources: [
  { id: 'pm', kind: 'chat', label: '共用 PM', space_id: 'spaces/pm', category: 'pm' },
] }
const scope: InquiryScope = {
  schema_version: 1, owner_viewer_id: 1, mention_id: 7, message_name: 'spaces/q/messages/7',
  target_space_id: 'spaces/q', target_thread_name: 'spaces/q/threads/7',
  profile_id: 1, profile_name: '甲案', revision: 3,
  sources: [{ ...profile.sources[0], use: 'reply_context' }],
  limit: 50, code_terms: [], digest: 'fake-digest',
}
const me: Me = {
  viewer: { id: 1, google_user_id: 'users/fake', email: 'viewer@example.test', display_name: '測試 Viewer' },
  scopes: [], has_identity_scope: true, features: { project_context: true },
  preferences: { pinned_space_ids: [], default_limit: 50, default_style: 'general', project_context_opt_in: true },
  collector: null, mention_counts: { pending: 1, resolved: 0 },
}
function choose() {
  const store = useDraftStore.getState()
  store.activateMention(7)
  store.selectProject(profile)
  store.toggleProjectSource('pm')
  store.setProjectConsent({ spaceId: 'spaces/q', threadName: 'spaces/q/threads/7' })
}
function delayedStream() {
  let resolve!: () => void
  let callbacks: sse.SseHandlers = {}
  const promise = new Promise<void>((done) => { resolve = done })
  vi.spyOn(sse, 'streamSse').mockImplementation((_url, _body, handlers) => { callbacks = handlers; return promise })
  return { resolve: () => resolve(), callbacks: () => callbacks }
}
beforeEach(() => {
  vi.restoreAllMocks()
  useDraftStore.getState().reset()
  useAuthStore.setState({ me })
  vi.spyOn(api, 'storedDraft').mockRejectedValue(new ApiError(404, 'DRAFT_NOT_FOUND', '無草稿'))
})
it('選專案不自動核准來源，必須確認目標後才可生成', async () => {
  const stream = vi.spyOn(sse, 'streamSse').mockResolvedValue()
  const store = useDraftStore.getState()
  store.activateMention(7)
  store.selectProject(profile)
  expect(useDraftStore.getState().active?.projectChoice).toMatchObject({ approvedIds: [], consent: null })
  await store.generate(7)
  expect(stream).not.toHaveBeenCalled()
  store.setProjectConsent({ spaceId: 'spaces/q', threadName: 'spaces/q/threads/7' })
  await store.generate(7)
  expect(stream.mock.calls[0][1]).toMatchObject({ project_selection: {
    profile_id: 1, revision: 3, sources: [{ binding_id: 'pm', use: 'metadata_only' }],
    target_space_id: 'spaces/q', target_thread_name: 'spaces/q/threads/7',
  } })
  expect(stream.mock.calls[0][1]).not.toHaveProperty('reference_space_ids')
  expect(stream.mock.calls[0][1]).not.toHaveProperty('code_refs')
})
it.each(['project', 'legacy', 'source', 'reference', 'code', 'consent', 'limit', 'terms'])('同題更改 %s 換 epoch 並丟棄晚到 callback', async (change) => {
  choose()
  const delayed = delayedStream()
  const run = useDraftStore.getState().generate(7)
  const oldEpoch = useDraftStore.getState().active?.epoch
  delayed.callbacks().onMeta?.({ type: 'meta', project_scope: scope })
  delayed.callbacks().onChunk?.({ type: 'chunk', text: '### ✍️ 建議回話\n舊案' })
  const store = useDraftStore.getState()
  if (change === 'project') store.selectProject({ ...profile, id: 2, name: '乙案' })
  if (change === 'legacy') store.selectProject(null)
  if (change === 'source') store.toggleProjectSource('pm')
  if (change === 'reference') store.toggleReference('spaces/other')
  if (change === 'code') store.toggleCodeRef(3, 'production')
  if (change === 'consent') store.setProjectConsent(null)
  if (change === 'limit') store.setRefLimit('71')
  if (change === 'terms') store.setCodeTerms('other_symbol')
  expect(useDraftStore.getState().active?.epoch).not.toBe(oldEpoch)
  delayed.callbacks().onMeta?.({ type: 'meta', project_scope: scope })
  delayed.callbacks().onChunk?.({ type: 'chunk', text: '晚到舊案' })
  delayed.callbacks().onDone?.({ type: 'done', draft_id: 9, scope_digest: scope.digest, project_status: 'complete' })
  delayed.resolve()
  await run
  expect(useDraftStore.getState()).toMatchObject({ raw: '', meta: null, draftId: null, replyText: '' })
})
it('切 Mention 清除本題專案與同意', () => {
  choose()
  useDraftStore.getState().activateMention(8)
  expect(useDraftStore.getState().active?.projectChoice).toBeFalsy()
})
it('EOF 或沒有相符 done 的專案草稿保持 partial，禁止送出', async () => {
  choose()
  const delayed = delayedStream()
  const send = vi.spyOn(api, 'sendReply')
  const run = useDraftStore.getState().generate(7)
  delayed.callbacks().onMeta?.({ type: 'meta', project_scope: scope })
  delayed.callbacks().onChunk?.({ type: 'chunk', text: '### ✍️ 建議回話\n部分文字' })
  delayed.resolve()
  await run
  expect(useDraftStore.getState().projectStatus).toBe('partial')
  await expect(useDraftStore.getState().send(7)).rejects.toThrow()
  expect(send).not.toHaveBeenCalled()
})
it('還原只採歷史 scope，不恢復來源核准與同意，stale 拒送', async () => {
  vi.mocked(api.storedDraft).mockResolvedValue({
    draft_id: 90, mention_id: 7, content_md: '### ✍️ 建議回話\n歷史',
    scope_state: 'stale', generation_config: { project_status: 'complete', meta: { type: 'meta', project_scope: scope } },
    created_at: '', sent_at: null,
  })
  await useDraftStore.getState().loadStored(7)
  expect(useDraftStore.getState().active?.projectChoice).toBeFalsy()
  expect(useDraftStore.getState().meta?.project_scope).toEqual(scope)
  expect(useDraftStore.getState().projectStatus).toBe('stale')
  await expect(useDraftStore.getState().send(7)).rejects.toThrow()
})
it('送出引用生成 snapshot digest，人工編輯保留未語意查證範圍', async () => {
  choose()
  const delayed = delayedStream()
  const send = vi.spyOn(api, 'sendReply').mockResolvedValue({ status: 'sent', message_id: 'fake', createTime: '', mention: { id: 7, space_id: 'spaces/q', space_name: '問題', message_name: 'spaces/q/messages/7', thread_name: 'spaces/q/threads/7', sender_display: '提問者', create_time: '', state: 'resolved', resolved_at: '' } })
  const run = useDraftStore.getState().generate(7)
  delayed.callbacks().onMeta?.({ type: 'meta', project_scope: scope })
  delayed.callbacks().onChunk?.({ type: 'chunk', text: '### ✍️ 建議回話\n草稿' })
  delayed.callbacks().onDone?.({ type: 'done', draft_id: 91, scope_digest: scope.digest, project_status: 'complete' })
  delayed.resolve()
  await run
  useDraftStore.getState().setReplyText('人工編輯')
  await useDraftStore.getState().send(7)
  expect(send).toHaveBeenCalledWith(7, expect.objectContaining({ draft_id: 91, scope_digest: 'fake-digest', text: '人工編輯' }))
})

it('legacy 串流切到專案會清空並拒絕晚到結果', async () => {
  const delayed = delayedStream()
  const store = useDraftStore.getState()
  const run = store.generate(7)
  delayed.callbacks().onChunk?.({ type: 'chunk', text: '### ✍️ 建議回話\n舊回話' })
  store.setReplyText('手動編輯')
  store.selectProject(profile)
  delayed.callbacks().onChunk?.({ type: 'chunk', text: '晚到舊回話' })
  delayed.callbacks().onDone?.({ type: 'done', draft_id: 8, reply: '舊結果' })
  delayed.resolve()
  await run
  expect(useDraftStore.getState()).toMatchObject({ raw: '', replyText: '', draftId: null })
  expect(useDraftStore.getState().active?.projectChoice?.profile).toEqual(profile)
})

it.each(['reference', 'code', 'terms'])('僅有歷史專案 scope 時更改 %s 仍清空草稿', async (change) => {
  vi.mocked(api.storedDraft).mockResolvedValueOnce({
    draft_id: 90, mention_id: 7, content_md: '### ✍️ 建議回話\n歷史',
    scope_state: 'current', generation_config: { project_status: 'complete', meta: { type: 'meta', project_scope: scope } },
    created_at: '', sent_at: null,
  })
  const store = useDraftStore.getState()
  await store.loadStored(7)
  expect(useDraftStore.getState().active?.projectChoice).toBeFalsy()
  expect(useDraftStore.getState().draftId).toBe(90)
  if (change === 'reference') store.toggleReference('spaces/other')
  if (change === 'code') store.toggleCodeRef(3, 'production')
  if (change === 'terms') store.setCodeTerms('other_symbol')
  expect(useDraftStore.getState()).toMatchObject({ raw: '', replyText: '', draftId: null, meta: null })
})
