import { MERGE_MAX, type MergeBlockReason } from '@/lib/merge'

/**
 * 「為什麼這一則不能一起回」的文案（規格 §15.1）。
 *
 * **為什麼要與規則分家**：`merge.ts` 裡的 `mergeBlockReason()` 是規則，而規則
 * 有前後端兩份實作（後端 `resolve_merge_targets`），改一邊要改兩邊。文案不是
 * ——改文案不必動後端。把兩者放在同一個檔，review 時分不出「這個 diff 是在改
 * 規則還是在改說法」，而前者需要同步後端、後者不需要。分家之後那件事變成
 * 兩個檔案的 diff，一眼就看得出來。
 *
 * `merge.ts` 保留 re-export，既有呼叫端不必改（規格 §17 紅線 3：不擴大改動）。
 *
 * 這三句話是使用者判斷「我到底該怎麼回這幾則」的唯一依據，所以講的是**後果**
 * 而不是規則名稱——「不同討論串」對使用者沒有意義，「回話只會送到其中一串，
 * 另一串看不到」才有。
 */
export const MERGE_BLOCK_LABEL: Record<MergeBlockReason, string> = {
  'other-space': '不同的聊天室，沒辦法用一則回話回完',
  'other-thread': '同一個聊天室但不同討論串——回話只會送到其中一串，另一串看不到',
  'too-many': `一次最多合併 ${MERGE_MAX} 則`,
}
