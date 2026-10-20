import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '@/lib/api'
import type { Mention, ProjectProfile } from '@/lib/types'
import { useAuthStore } from '@/store/auth'
import { useCodeProjectStore } from '@/store/codeProjects'
import { useDraftStore } from '@/store/draft'
import { useSpacesStore } from '@/store/spaces'
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
