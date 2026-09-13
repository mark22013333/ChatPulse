import { useMemo } from 'react'
import { SparklesIcon, SquareIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useDraftStore } from '@/store/draft'
import { useMentionsStore } from '@/store/mentions'
import type { Mention } from '@/lib/types'

/**
 * 產生／停止串流（規格 §9.2）。
 *
 * 串流中換成「停止串流」而不是把按鈕鎖住：使用者要有一個明確的中止入口。
 * 卸載時**不會**自動中止（見 `DraftReplyWorkspace` 的註解）——停止是使用者
 * 按下去表達的意圖，不是切個頁籤的副作用。
 */
export function GenerateButton({ mention }: { mention: Mention }) {
  const streaming = useDraftStore((s) => s.streaming)
  const raw = useDraftStore((s) => s.raw)
  const refLimitError = useDraftStore((s) => s.refLimitError)
  const generate = useDraftStore((s) => s.generate)
  const abort = useDraftStore((s) => s.abort)
  const mergeIds = useMentionsStore((s) => s.mergeIds)

  // 只有「這一則自己也在勾選裡」時才算在合併——否則收件匣勾了 A、B，
  // 使用者卻點開 C 去按產生，會把不相干的 A、B 一起回掉
  const activeMergeIds = useMemo(
    () => (mergeIds.includes(mention.id) ? mergeIds : []),
    [mergeIds, mention.id],
  )

  return (
    <div className="shrink-0 border-t border-border p-2.5">
      {streaming ? (
        <Button variant="outline" className="w-full" onClick={abort}>
          <SquareIcon />
          停止串流
        </Button>
      ) : (
        <Button
          className="w-full"
          // 收件匣勾了要合併的話，這顆按鈕也要照著合併——不然
          // 「勾了兩則卻只回到一則」是靜默的，使用者要送出後才發現
          onClick={() => void generate(mention.id, activeMergeIds)}
          disabled={Boolean(refLimitError)}
        >
          <SparklesIcon />
          {activeMergeIds.length > 1
            ? `${raw ? '重新產生' : '產生'} Draft Reply（合併 ${activeMergeIds.length} 則）`
            : raw
              ? '重新產生 Draft Reply'
              : '產生 Draft Reply'}
        </Button>
      )}
    </div>
  )
}
