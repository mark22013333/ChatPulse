import { SparklesIcon, XIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { Mention } from '@/lib/types'

interface MergeBarProps {
  /** 勾選的順序就是「誰是主要那則」——回話會送到第一則所在的討論串。 */
  selected: Mention[]
  onCancel: () => void
  onGenerate: () => void
}

/**
 * 收件匣的合併列。
 *
 * **只在真的勾了東西時才出現**——常駐一條工具列會讓「單則回覆」這個絕大多數
 * 的情況每次都要多看一行。所以呼叫端負責「有沒有勾」的判斷，這裡假設一定有。
 *
 * 這一列的重點是最下面那句話：**回話只會送到第一則所在的討論串**，而其餘幾則
 * 會一起被標成已處理。那件事從草稿內容本身完全看不出來，一定要講在按下去之前。
 */
export function MergeBar({ selected, onCancel, onGenerate }: MergeBarProps) {
  return (
    // 表面分層改版刻意**沒有**把這一條換成 `bg-raised`（設計稿原本這樣提議）。
    // `bg-signal-wash` 同時承載「現在有東西被選中了」這個狀態訊號，而 raised
    // 只說得出「這是操作面」。設計原則§2.2 第 2 條說顏色只給「可互動與當前選取」
    // ——合併列正是選取的結果，這裡用 accent 底是它該有的位置。
    <div className="shrink-0 space-y-1.5 border-b border-signal-line bg-signal-wash px-3 py-2">
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium">已選 {selected.length} 則，一起回成一則</span>
        <Button size="xs" variant="ghost" className="ml-auto" onClick={onCancel}>
          <XIcon />
          取消
        </Button>
      </div>
      <Button size="xs" className="w-full" disabled={selected.length < 2} onClick={onGenerate}>
        <SparklesIcon />
        {selected.length < 2 ? '再勾一則才需要合併' : `合併產生草稿（${selected.length} 則）`}
      </Button>
      <p className="text-2xs leading-relaxed text-muted-foreground">
        回話會送到「{selected[0]?.space_name}」，送出後這 {selected.length} 則會一起標成已處理。
      </p>
    </div>
  )
}
