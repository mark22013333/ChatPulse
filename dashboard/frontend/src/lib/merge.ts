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

/**
 * 文案住在 `lib/mergeCopy.ts`（規格 §15.1）。
 *
 * 分家的理由：**規則有前後端兩份實作，文案沒有。** 改 `mergeBlockReason()`
 * 要同步後端的 `resolve_merge_targets`，改說法不必——兩件事放在同一個檔，
 * review 時分不出這個 diff 屬於哪一種。
 *
 * **這裡刻意不做 re-export**（規格原文寫「`merge.ts` 只保留 re-export」）：
 * `mergeCopy.ts` 要用這個檔的 `MERGE_MAX` 組「一次最多合併 N 則」，
 * 再從這裡 re-export 就形成循環 import——`MERGE_MAX` 會落在 TDZ 裡，
 * 而它是否炸掉取決於 bundler 有沒有把那個 const 提前。那種「在 vitest 裡好、
 * 在某個建置設定下壞」的東西不值得留。當時 MERGE_BLOCK_LABEL 只有一個
 * 呼叫端（`components/inbox/MentionCard.tsx`），直接讓它改 import 更乾淨：
 * 「拿文案」與「拿規則」變成兩行看得出差別的 import。
 */

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
