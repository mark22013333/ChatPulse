import type { Mention, Space } from '@/lib/types'

/**
 * 一次最多合併幾則。與後端 `server.MERGE_MAX` 是同一個數字——
 * 兩邊分歧的話，使用者會勾到第 6 則才被伺服器擋下來，而且錯誤訊息出現在
 * 按下按鈕之後，那時他已經花了力氣選。
 */
export const MERGE_MAX = 5

/**
 * 這個 Space 的討論串是不是對話單位？回 true 代表「不是」（扁平）。
 *
 * 與後端 `core/draft_context.is_flat_space()` 是**同一條規則**，必須一起改。
 * 兩個訊號取聯集的理由：2026-09-06 實測 436 個 Space，私訊回報的
 * `spaceThreadingState` 是 `THREADED_MESSAGES`（不是官方文件寫的
 * `UNTHREADED_MESSAGES`），只看那個欄位會把所有私訊判成分串。
 */
export function isFlatSpace(space: Space | undefined): boolean {
  if (!space) return false
  if ((space.threadingState ?? '').toUpperCase() === 'UNTHREADED_MESSAGES') return true
  return (space.type ?? '').toUpperCase() === 'DIRECT_MESSAGE'
}

/** 不能一起回的原因。回 null 代表可以合併。 */
export type MergeBlockReason = 'other-space' | 'other-thread' | 'too-many'

export const MERGE_BLOCK_LABEL: Record<MergeBlockReason, string> = {
  'other-space': '不同的聊天室，沒辦法用一則回話回完',
  'other-thread': '同一個聊天室但不同討論串——回話只會送到其中一串，另一串看不到',
  'too-many': `一次最多合併 ${MERGE_MAX} 則`,
}

/**
 * `candidate` 能不能加進目前已勾選的這一組？
 *
 * 規則與後端 `resolve_merge_targets()` 一致：同一個 Space；分串聊天室還要
 * 同一個討論串。**前端這份是為了好用**（不能勾的先變灰並說明原因），
 * 正確性仍由後端把關——前端規則永遠可能被繞過。
 */
export function mergeBlockReason(
  candidate: Mention,
  selected: Mention[],
  spaces: Space[],
): MergeBlockReason | null {
  if (selected.length === 0) return null
  if (selected.some((m) => m.id === candidate.id)) return null // 已勾選的可以取消
  if (selected.length >= MERGE_MAX) return 'too-many'

  const anchor = selected[0]
  if (candidate.space_id !== anchor.space_id) return 'other-space'

  const space = spaces.find((s) => s.id === anchor.space_id)
  if (!isFlatSpace(space) && candidate.thread_name !== anchor.thread_name) {
    return 'other-thread'
  }
  return null
}
