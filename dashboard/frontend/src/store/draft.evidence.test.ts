import { beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '@/lib/api'
import * as sse from '@/lib/sse'
import type { InquiryScope, Me, ProjectProfile, ReplyAssessment } from '@/lib/types'
import { useAuthStore } from './auth'
import { useDraftStore } from './draft'

const profile: ProjectProfile = { id: 4, name: '測試專案', revision: 2, sources: [
  { id: 'private', kind: 'chat', label: '私有 Space', space_id: 'spaces/private', category: 'space' },
  { id: 'public', kind: 'chat', label: '可引用 Space', space_id: 'spaces/public', category: 'space' },
] }
const scope: InquiryScope = {
  schema_version: 1, owner_viewer_id: 1, mention_id: 7, message_name: 'spaces/q/messages/7',
  target_space_id: 'spaces/q', target_thread_name: 'spaces/q/threads/7', profile_id: 4,
  profile_name: '測試專案', revision: 2, sources: [
    { ...profile.sources[0], use: 'analysis_only' }, { ...profile.sources[1], use: 'reply_context' },
  ], limit: 50, code_terms: [], environment: 'uat', digest: 'scope-uat',
}
const assessment: ReplyAssessment = { status: 'evaluated', method: 'model_assisted_v1', text_hash: 'hash',
  claims: [{ id: 'c1', text: 'UAT 已部署', kind: 'status', relation: 'supported', reference_validity: 'valid',
    currency: 'current', scope: 'match', reason: '測試來源支持', citations: [{ evidence_id: 'e1', locator: 'messages/1', revision: 'r1' }] }],
  gaps: [], stop_reason: null,
}
const sentMention = { id: 7, space_id: 'spaces/q', space_name: '問題', message_name: 'spaces/q/messages/7',
  thread_name: 'spaces/q/threads/7', sender_display: '提問者', create_time: '', state: 'resolved' as const, resolved_at: '' }
const me: Me = { viewer: { id: 1, google_user_id: 'users/fake', email: 'viewer@example.test', display_name: 'Viewer' },
  scopes: [], has_identity_scope: true, features: { project_context: true, reply_evidence: true },
  preferences: { pinned_space_ids: [], default_limit: 50, default_style: 'general', project_context_opt_in: true },
  collector: null, mention_counts: { pending: 1, resolved: 0 },
}
function choose() {
  const store = useDraftStore.getState()
  store.activateMention(7)
  store.selectProject(profile)
  store.setProjectSourceUse('private', 'analysis_only')
  store.setProjectSourceUse('public', 'reply_context')
  store.setProjectEnvironment('uat')
  store.setProjectConsent({ spaceId: 'spaces/q', threadName: 'spaces/q/threads/7' })
}
function pendingStream() {
  let finish!: () => void
  let handlers: sse.SseHandlers = {}
  const mock = vi.spyOn(sse, 'streamSse').mockImplementation((_url, _body, callbacks) => {
    handlers = callbacks
    return new Promise<void>((resolve) => { finish = resolve })
  })
  return { handlers: () => handlers, finish: () => finish(), mock }
}
async function complete() {
  const stream = pendingStream()
  const run = useDraftStore.getState().generate(7)
  stream.handlers().onMeta?.({ type: 'meta', project_scope: scope })
  stream.handlers().onReviewProgress?.({ type: 'review_progress', stage: 'reviewing', mention_id: 7,
    scope_digest: scope.digest, counts: { claims: 1 } })
  stream.handlers().onChunk?.({ type: 'chunk', text: '私有分析，不得顯示' })
  expect(useDraftStore.getState()).toMatchObject({ raw: '', replyText: '', receiptId: null })
  stream.handlers().onDone?.({ type: 'done', draft_id: 91, scope_digest: scope.digest,
    project_status: 'complete', reply: 'UAT 已部署', assessment, receipt_id: 'receipt-1', review_revision: 1 })
  stream.finish()
  await run
  return stream
}
beforeEach(() => {
  vi.restoreAllMocks()
  useDraftStore.getState().reset()
  useAuthStore.setState({ me })
  vi.spyOn(api, 'storedDraft').mockRejectedValue(new ApiError(404, 'DRAFT_NOT_FOUND', '無草稿'))
})
it('三態來源與環境進入同一請求；改環境撤銷同意及可送出收據', async () => {
  choose()
  const stream = await complete()
  expect(stream.mock.mock.calls[0][1]).toMatchObject({ project_selection: {
    environment: 'uat', sources: [
      { binding_id: 'private', use: 'analysis_only' }, { binding_id: 'public', use: 'reply_context' },
    ], target_space_id: 'spaces/q',
  } })
  expect(useDraftStore.getState()).toMatchObject({ replyText: 'UAT 已部署', receiptId: 'receipt-1', reviewProgress: null })
  expect(useDraftStore.getState().raw).not.toContain('私有分析')
  useDraftStore.getState().setProjectEnvironment('production')
  expect(useDraftStore.getState().active?.projectChoice).toMatchObject({ environment: 'production', consent: null })
  expect(useDraftStore.getState()).toMatchObject({ receiptId: null, draftId: null, replyText: '' })
})
it('改字立即失效收據，重查保留原樣文字並將收據帶到送出', async () => {
  choose()
  await complete()
  useDraftStore.getState().setReplyText('人工補充：UAT 尚未部署')
  const send = vi.spyOn(api, 'sendReply').mockResolvedValue({ status: 'sent', message_id: 'fake', createTime: '', mention: sentMention })
  await expect(useDraftStore.getState().send(7)).rejects.toThrow()
  expect(send).not.toHaveBeenCalled()
  vi.spyOn(api, 'reviewDraft').mockResolvedValue({ draft_id: 91, text_hash: 'new-hash',
    review_revision: 2, receipt_id: 'receipt-2', assessment: { ...assessment, text_hash: 'new-hash' } })
  await useDraftStore.getState().review(7)
  expect(api.reviewDraft).toHaveBeenCalledWith(7, { draft_id: 91, text: '人工補充：UAT 尚未部署', scope_digest: 'scope-uat' })
  expect(useDraftStore.getState().replyText).toBe('人工補充：UAT 尚未部署')
  await useDraftStore.getState().send(7)
  expect(send).toHaveBeenCalledWith(7, expect.objectContaining({ text: '人工補充：UAT 尚未部署',
    receipt_id: 'receipt-2', review_revision: 2 }))
})
it('後開始的重查優先；舊回應與換題後的回應都不能恢復收據', async () => {
  choose()
  await complete()
  useDraftStore.getState().setReplyText('人工版本')
  const finishes: Array<(value: Awaited<ReturnType<typeof api.reviewDraft>>) => void> = []
  vi.spyOn(api, 'reviewDraft').mockImplementation(() => new Promise((resolve) => { finishes.push(resolve) }))
  const first = useDraftStore.getState().review(7)
  const second = useDraftStore.getState().review(7)
  finishes[1]({ draft_id: 91, text_hash: 'hash-2', review_revision: 3, receipt_id: 'new', assessment })
  await second
  finishes[0]({ draft_id: 91, text_hash: 'hash-1', review_revision: 2, receipt_id: 'old', assessment })
  await first
  expect(useDraftStore.getState()).toMatchObject({ receiptId: 'new', reviewRevision: 3, replyText: '人工版本' })
  const third = useDraftStore.getState().review(7)
  useDraftStore.getState().activateMention(8)
  finishes[2]({ draft_id: 91, text_hash: 'hash-3', review_revision: 4, receipt_id: 'late', assessment })
  await third
  expect(useDraftStore.getState()).toMatchObject({ receiptId: null, draftId: null })
})
it('新旗標遇到舊專案草稿無收據會拒送，關閉旗標維持舊行為', async () => {
  vi.mocked(api.storedDraft).mockResolvedValue({ draft_id: 91, mention_id: 7, content_md: '舊回話',
    scope_state: 'current', generation_config: { project_status: 'complete', meta: { type: 'meta', project_scope: scope } },
    created_at: '', sent_at: null })
  await useDraftStore.getState().loadStored(7)
  const send = vi.spyOn(api, 'sendReply').mockResolvedValue({ status: 'sent', message_id: 'fake', createTime: '', mention: sentMention })
  await expect(useDraftStore.getState().send(7)).rejects.toThrow()
  expect(send).not.toHaveBeenCalled()
  useAuthStore.setState({ me: { ...me, features: { project_context: true, reply_evidence: false } } })
  await useDraftStore.getState().send(7)
  expect(send).toHaveBeenCalledWith(7, expect.not.objectContaining({ receipt_id: expect.anything() }))
})
