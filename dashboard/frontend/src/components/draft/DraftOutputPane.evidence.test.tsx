import { render, screen } from '@testing-library/react'
import { beforeEach, expect, it } from 'vitest'
import type { InquiryScope, Me, ReplyAssessment } from '@/lib/types'
import { useAuthStore } from '@/store/auth'
import { useDraftStore } from '@/store/draft'
import { DraftOutputPane } from './DraftOutputPane'

const me: Me = { viewer: { id: 1, google_user_id: 'users/fake', email: 'viewer@example.test', display_name: 'Viewer' },
  scopes: [], has_identity_scope: true, features: { project_context: true, reply_evidence: true },
  preferences: { pinned_space_ids: [], default_limit: 50, default_style: 'general', project_context_opt_in: true },
  collector: null, mention_counts: { pending: 1, resolved: 0 },
}
const scope: InquiryScope = { schema_version: 1, owner_viewer_id: 1, mention_id: 7,
  message_name: 'spaces/q/messages/7', target_space_id: 'spaces/q', target_thread_name: null,
  profile_id: 2, profile_name: '甲案', revision: 1, sources: [
    { id: 's1', kind: 'chat', label: '私有來源', space_id: 'spaces/private', category: 'space', use: 'analysis_only' },
    { id: 's2', kind: 'chat', label: '公開來源', space_id: 'spaces/public', category: 'space', use: 'reply_context' },
  ], environment: 'uat', limit: 50, code_terms: [], digest: 'scope',
}
const assessment: ReplyAssessment = { status: 'evaluated', method: 'model_assisted_v1', text_hash: 'hash',
  claims: [{ id: 'c1', text: 'UAT 已部署', kind: 'status', relation: 'insufficient',
    reference_validity: 'unresolved', currency: 'unknown', scope: 'mismatch', reason: '缺少該環境版本資訊',
    citations: [{ evidence_id: 'e1', locator: 'messages/1', revision: 'r1' }] }],
  gaps: ['缺少 UAT 部署紀錄'], stop_reason: null,
}
beforeEach(() => {
  useDraftStore.getState().reset()
  useDraftStore.getState().activateMention(7)
  useAuthStore.setState({ me })
})
it('公開評估呈現逐項關係、引用與缺口，並隱藏中途私有分析', () => {
  useDraftStore.setState({ meta: { type: 'meta', project_scope: scope }, projectStatus: 'complete',
    raw: '### 🧭 脈絡分析\n私有模型推理', replyText: 'UAT 已部署', assessment, receiptId: null, draftId: 9 })
  render(<DraftOutputPane active onRequestSend={() => {}} />)
  expect(screen.getByRole('region', { name: '回話評估' })).toBeTruthy()
  expect(screen.getByText(/支持不足/)).toBeTruthy()
  expect(screen.getByText(/引用：messages\/1/)).toBeTruthy()
  expect(screen.getByText('缺少 UAT 部署紀錄')).toBeTruthy()
  expect(screen.queryByText('私有模型推理')).toBeNull()
  expect(screen.getByRole('button', { name: '送出回話' })).toBeDisabled()
})
