import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '@/lib/api'
import type { Mention, ProjectProfile } from '@/lib/types'
import { useAuthStore } from '@/store/auth'
import { useCodeProjectStore } from '@/store/codeProjects'
import { useDraftStore } from '@/store/draft'
import { useSpacesStore } from '@/store/spaces'
import * as sse from '@/lib/sse'
import { DraftReplyEditor } from './DraftReplyEditor'
import { ProjectContextPanel } from './ProjectContextPanel'

const mention: Mention = { id: 7, space_id: 'spaces/q', space_name: '問題', message_name: 'spaces/q/messages/7',
  thread_name: 'spaces/q/threads/7', sender_display: '提問者', create_time: '', state: 'pending', resolved_at: null }
const profile: ProjectProfile = { id: 1, name: '甲案', revision: 1, sources: [
  { id: 'pm', label: '共用 PM', kind: 'chat', space_id: 'spaces/pm', category: 'pm' },
] }
beforeEach(() => {
  vi.restoreAllMocks()
  useDraftStore.getState().reset()
  useAuthStore.setState({ me: {
    viewer: { id: 1, google_user_id: 'users/fake', email: 'viewer@example.test', display_name: 'Viewer' },
    scopes: [], has_identity_scope: true, preferences: { default_limit: 50, default_style: 'general', pinned_space_ids: [], project_context_opt_in: true },
    features: { project_context: true }, collector: null, mention_counts: { pending: 1, resolved: 0 },
  } })
  useSpacesStore.setState({ items: [{ id: 'spaces/pm', displayName: '共用 PM', type: 'DIRECT_MESSAGE', lastActiveTime: null }] })
  useCodeProjectStore.setState({ loaded: true, projects: [] })
  vi.spyOn(api, 'projectProfiles').mockResolvedValue({ profiles: [profile] })
  vi.spyOn(api, 'storedDraft').mockRejectedValue(new ApiError(404, 'DRAFT_NOT_FOUND', '無草稿'))
  useDraftStore.getState().activateMention(7)
})
it.each(['mention', 'project'])('保存回應晚到 %s 後，只更新列表，不替換本題專案', async (change) => {
  let finish!: (profile: ProjectProfile) => void
  vi.spyOn(api, 'saveProjectProfile').mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  render(<ProjectContextPanel mention={mention} />)
  await screen.findByRole('option', { name: '甲案' })
  fireEvent.change(screen.getByLabelText('編輯已存專案'), { target: { value: '1' } })
  fireEvent.click(screen.getByText('管理專案來源'))
  fireEvent.click(screen.getByRole('button', { name: '更新專案來源' }))
  await waitFor(() => expect(api.saveProjectProfile).toHaveBeenCalled())
  act(() => {
    if (change === 'mention') useDraftStore.getState().activateMention(8)
    else useDraftStore.getState().selectProject({ ...profile, id: 2, name: '乙案' })
  })
  const epoch = useDraftStore.getState().active?.epoch
  await act(async () => { finish({ ...profile, revision: 2 }); await Promise.resolve() })
  expect(useDraftStore.getState().active?.epoch).toBe(epoch)
  if (change === 'mention') expect(useDraftStore.getState().active?.projectChoice).toBeFalsy()
  else expect(useDraftStore.getState().active?.projectChoice?.profile.name).toBe('乙案')
  expect(screen.getByRole('option', { name: '甲案 · r2' })).toBeTruthy()
})
it('flag 關閉時不新增 profile API requests', () => {
  useAuthStore.setState({ me: null })
  render(<ProjectContextPanel mention={mention} />)
  expect(api.projectProfiles).not.toHaveBeenCalled()
})


it('逐字輸入相對路徑清單時保留逗號，保存時解析成路徑陣列', async () => {
  const gitProfile: ProjectProfile = { id: 2, name: 'Git 案', revision: 1, sources: [{
    id: 'g', kind: 'git', label: 'Git', code_project_id: 3, environment: 'production', paths: ['src/a.py'],
  }] }
  vi.mocked(api.projectProfiles).mockResolvedValue({ profiles: [gitProfile] })
  vi.spyOn(api, 'saveProjectProfile').mockResolvedValue({ ...gitProfile, revision: 2 })
  render(<ProjectContextPanel mention={mention} />)
  await screen.findByRole('option', { name: 'Git 案' })
  fireEvent.click(screen.getByText('管理專案來源'))
  fireEvent.change(screen.getByLabelText('編輯已存專案'), { target: { value: '2' } })
  const input = screen.getByLabelText('Git 相對路徑')
  fireEvent.change(input, { target: { value: 'src/a.py,' } })
  expect(input).toHaveValue('src/a.py,')
  fireEvent.change(input, { target: { value: 'src/a.py, src/b.py' } })
  fireEvent.click(screen.getByRole('button', { name: '更新專案來源' }))
  await waitFor(() => expect(api.saveProjectProfile).toHaveBeenCalledWith(expect.objectContaining({
    sources: [expect.objectContaining({ paths: ['src/a.py', 'src/b.py'] })],
  }), 2))
})

const otherProfile: ProjectProfile = { ...profile, id: 2, name: '乙案' }
function storedManual(mentionId: number) {
  return { draft_id: 90, mention_id: mentionId, content_md: '已還原回話', generation_config: {}, created_at: '', sent_at: null }
}
function pendingStream() {
  let finish!: () => void
  let callbacks: sse.SseHandlers = {}
  let signal: AbortSignal | undefined
  vi.spyOn(sse, 'streamSse').mockImplementation((_url, _body, handlers, abortSignal) => {
    callbacks = handlers
    signal = abortSignal
    return new Promise<void>((resolve) => { finish = resolve })
  })
  return { callbacks: () => callbacks, signal: () => signal, finish: () => finish() }
}
it.each(['restored', 'streaming'])('編輯甲案保存時保留本題乙案的 %s 草稿', async (mode) => {
  vi.mocked(api.projectProfiles).mockResolvedValue({ profiles: [profile, otherProfile] })
  vi.spyOn(api, 'saveProjectProfile').mockResolvedValue({ ...profile, revision: 2 })
  const stream = pendingStream()
  const store = useDraftStore.getState()
  store.selectProject(otherProfile)
  store.setProjectConsent({ spaceId: mention.space_id, threadName: mention.thread_name ?? null })
  let run: Promise<void> | undefined
  if (mode === 'restored') {
    vi.mocked(api.storedDraft).mockResolvedValue(storedManual(7))
    await store.loadStored(7)
  } else {
    run = store.generate(7)
    stream.callbacks().onChunk?.({ type: 'chunk', text: '生成中' })
  }
  store.setReplyText('乙案人工編輯')
  const before = useDraftStore.getState()
  render(<ProjectContextPanel mention={mention} />)
  await screen.findByRole('option', { name: '甲案' })
  fireEvent.click(screen.getByText('管理專案來源'))
  fireEvent.change(screen.getByLabelText('編輯已存專案'), { target: { value: '1' } })
  fireEvent.click(screen.getByRole('button', { name: '更新專案來源' }))
  await screen.findByRole('option', { name: '甲案 · r2' })
  expect(useDraftStore.getState().active?.projectChoice?.profile.id).toBe(2)
  expect(useDraftStore.getState()).toMatchObject({ replyText: before.replyText, draftId: before.draftId, raw: before.raw })
  expect(useDraftStore.getState().active).toBe(before.active)
  if (run) {
    expect(stream.signal()?.aborted).toBe(false)
    await act(async () => { stream.callbacks().onDone?.({ type: 'done', draft_id: 99 }); stream.finish(); await run })
    expect(useDraftStore.getState().draftId).toBe(99)
  }
})
it('更新本題甲案後清除舊版核准、同意與草稿', async () => {
  vi.spyOn(api, 'saveProjectProfile').mockResolvedValue({ ...profile, revision: 2 })
  const store = useDraftStore.getState()
  store.selectProject(profile)
  store.toggleProjectSource('pm')
  store.setProjectConsent({ spaceId: mention.space_id, threadName: mention.thread_name ?? null })
  store.setReplyText('甲案舊版回話')
  render(<ProjectContextPanel mention={mention} />)
  await screen.findByRole('option', { name: '甲案' })
  fireEvent.click(screen.getByText('管理專案來源'))
  fireEvent.change(screen.getByLabelText('編輯已存專案'), { target: { value: '1' } })
  fireEvent.click(screen.getByRole('button', { name: '更新專案來源' }))
  await screen.findByRole('option', { name: '甲案 · r2' })
  expect(useDraftStore.getState().active?.projectChoice).toMatchObject({ profile: { id: 1, revision: 2 }, approvedIds: [], consent: null })
  expect(useDraftStore.getState()).toMatchObject({ replyText: '', draftId: null, meta: null })
})
it('建立新專案不替本題選用或清除人工草稿', async () => {
  vi.spyOn(api, 'saveProjectProfile').mockResolvedValue(otherProfile)
  useDraftStore.getState().setReplyText('保留人工回話')
  render(<ProjectContextPanel mention={mention} />)
  await screen.findByRole('option', { name: '甲案' })
  fireEvent.click(screen.getByText('管理專案來源'))
  fireEvent.change(screen.getByLabelText('專案名稱'), { target: { value: '乙案' } })
  fireEvent.change(screen.getByLabelText('新增 Space 來源'), { target: { value: 'spaces/pm' } })
  fireEvent.click(screen.getByRole('button', { name: '保存新專案' }))
  await screen.findByRole('option', { name: '乙案 · r1' })
  expect(useDraftStore.getState().active?.projectChoice).toBeFalsy()
  expect(useDraftStore.getState().replyText).toBe('保留人工回話')
})
it('相同 Git binding ID 的兩案切換時顯示並保存各自路徑', async () => {
  const a: ProjectProfile = { ...profile, sources: [{ id: 'git', kind: 'git', label: 'Git', code_project_id: 3, environment: 'production', paths: ['src/a.py'] }] }
  const b: ProjectProfile = { ...otherProfile, sources: [{ id: 'git', kind: 'git', label: 'Git', code_project_id: 3, environment: 'production', paths: ['src/b.py'] }] }
  vi.mocked(api.projectProfiles).mockResolvedValue({ profiles: [a, b] })
  vi.spyOn(api, 'saveProjectProfile').mockResolvedValue({ ...b, revision: 2 })
  render(<ProjectContextPanel mention={mention} />)
  await screen.findByRole('option', { name: '甲案' })
  fireEvent.click(screen.getByText('管理專案來源'))
  fireEvent.change(screen.getByLabelText('編輯已存專案'), { target: { value: '1' } })
  expect(screen.getByLabelText('Git 相對路徑')).toHaveValue('src/a.py')
  fireEvent.change(screen.getByLabelText('編輯已存專案'), { target: { value: '2' } })
  expect(screen.getByLabelText('Git 相對路徑')).toHaveValue('src/b.py')
  fireEvent.click(screen.getByRole('button', { name: '更新專案來源' }))
  await waitFor(() => expect(api.saveProjectProfile).toHaveBeenCalledWith(expect.objectContaining({ sources: b.sources }), 2))
})
it.each([true, false].flatMap((initial) => ['restored', 'edited', 'streaming'].map((mode) => ({ initial, mode }))))('偏好 PATCH 晚到保留另一題一般草稿 $initial $mode', async ({ initial, mode }) => {
  const me = useAuthStore.getState().me
  if (!me) throw new Error('測試缺少 Viewer')
  useAuthStore.setState({ me: { ...me, preferences: { ...me.preferences, project_context_opt_in: initial } } })
  let finish!: (value: Awaited<ReturnType<typeof api.projectOptIn>>) => void
  vi.spyOn(api, 'projectOptIn').mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  const refresh = vi.spyOn(useAuthStore.getState(), 'refreshMe').mockResolvedValue()
  const stream = pendingStream()
  const panel = render(<ProjectContextPanel mention={mention} />)
  fireEvent.click(screen.getByLabelText('啟用我的專案脈絡'))
  expect(api.projectOptIn).toHaveBeenCalledWith(!initial)
  panel.unmount()
  const store = useDraftStore.getState()
  vi.mocked(api.storedDraft).mockResolvedValue(storedManual(8))
  await act(async () => { await store.loadStored(8) })
  let run: Promise<void> | undefined
  if (mode === 'streaming') run = store.generate(8)
  if (mode !== 'restored') store.setReplyText('乙題人工編輯')
  const before = useDraftStore.getState()
  await act(async () => { finish({ ...me.preferences, project_context_opt_in: !initial }); await Promise.resolve() })
  expect(refresh).toHaveBeenCalledOnce()
  expect(useDraftStore.getState().replyText).toBe(before.replyText)
  expect(useDraftStore.getState().draftId).toBe(before.draftId)
  expect(useDraftStore.getState().active).toBe(before.active)
  if (run) {
    expect(stream.signal()?.aborted).toBe(false)
    stream.callbacks().onDone?.({ type: 'done', draft_id: 99, reply: '晚到結果' })
    stream.finish()
    await run
    expect(useDraftStore.getState().draftId).toBe(99)
    expect(useDraftStore.getState().replyText).toBe('乙題人工編輯')
  }
})
it.each(['same', 'other'])('停用完成會撤銷目前專案草稿 %s', async (target) => {
  let finish!: (value: Awaited<ReturnType<typeof api.projectOptIn>>) => void
  vi.spyOn(api, 'projectOptIn').mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  vi.spyOn(useAuthStore.getState(), 'refreshMe').mockResolvedValue()
  const store = useDraftStore.getState()
  store.selectProject(profile)
  render(<ProjectContextPanel mention={mention} />)
  fireEvent.click(screen.getByLabelText('啟用我的專案脈絡'))
  if (target === 'other') act(() => { store.activateMention(8); store.selectProject(otherProfile) })
  act(() => { store.setReplyText('停用前專案回話') })
  const prefs = useAuthStore.getState().me?.preferences
  if (!prefs) throw new Error('測試缺少 Viewer')
  await act(async () => { finish({ ...prefs, project_context_opt_in: false }); await Promise.resolve() })
  expect(useDraftStore.getState().active?.projectChoice).toBeNull()
  expect(useDraftStore.getState().replyText).toBe('')
})
it.each(['feature', 'opt-in'])('重新載入時 %s 關閉的專案草稿保留 snapshot 且禁止送出', async (gate) => {
  const me = useAuthStore.getState().me
  if (!me) throw new Error('測試缺少 Viewer')
  useAuthStore.setState({ me: { ...me, features: { project_context: gate !== 'feature' }, preferences: { ...me.preferences, project_context_opt_in: gate !== 'opt-in' } } })
  vi.mocked(api.storedDraft).mockResolvedValue({ ...storedManual(7), scope_state: 'current', generation_config: {
    project_status: 'complete', meta: { type: 'meta', project_scope: {
      schema_version: 1, owner_viewer_id: 1, mention_id: 7, message_name: mention.message_name,
      target_space_id: mention.space_id, target_thread_name: mention.thread_name ?? null,
      profile_id: 1, profile_name: '甲案', revision: 1, sources: [], limit: 50, code_terms: [], digest: 'fake',
    } },
  } })
  await useDraftStore.getState().loadStored(7)
  render(<DraftReplyEditor onRequestSend={vi.fn()} />)
  expect(screen.getByRole('button', { name: '送出回話' })).toBeDisabled()
  expect(useDraftStore.getState().projectStatus).toBe('stale')
  expect(useDraftStore.getState().meta?.project_scope?.profile_name).toBe('甲案')
})
