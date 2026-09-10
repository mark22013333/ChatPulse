import { useEffect } from 'react'
import { SummaryOutput } from '@/components/summary/SummaryOutput'
import { SummaryToolbar } from '@/components/summary/SummaryToolbar'
import { useSummaryStore } from '@/store/summary'
import type { Space } from '@/lib/types'

interface SummaryWorkspaceProps {
  space: Space | null
  /** 草稿目標建立好、已開始生成時呼叫，由外層導航到草稿工作區 */
  onDraftCreated?: (mentionId: number) => void
  /**
   * 這個工作台目前看得見嗎。兩個工作台常駐掛載之後，看不見的那一半仍會收到
   * 每個串流 chunk；傳下去讓 Markdown 在不可見時暫停重新 parse（§7.3）。
   */
  active?: boolean
}

/**
 * 摘要工作台。**這個檔只留版面骨架與換 Space 的 reset 契約**（規格 §9.2 給
 * `DraftReplyWorkspace` 的形狀，這裡照同一套做）：工具列、產出區與推播確認
 * 各自成檔，自己用細 selector 讀 store（§9.3）。
 */
export function SummaryWorkspace({ space, onDraftCreated, active = true }: SummaryWorkspaceProps) {
  // store 記著「目前這份摘要是哪個 Space 的」，用它判斷要不要清空
  const streamedSpaceId = useSummaryStore((s) => s.streamedSpaceId)
  const limitError = useSummaryStore((s) => s.limitError)
  const reset = useSummaryStore((s) => s.reset)

  // 只有**真的換了 Space** 才清空。
  //
  // 以前這裡是無條件 reset()，而 App.tsx 的頁籤是條件渲染（不是隱藏），
  // 切頁籤會把這個元件整個卸載重掛——掛載時的 reset() 就把還在串流的
  // 摘要清光了。原註解擔心的 setState-after-unmount 不會發生：狀態在
  // zustand store 裡，不是元件狀態。
  useEffect(() => {
    const id = space?.id ?? null
    if (id !== null && streamedSpaceId !== null && id !== streamedSpaceId) {
      reset()
    }
  }, [space?.id, streamedSpaceId, reset])

  // 這裡刻意**不**在卸載時 abort：理由同 DraftReplyWorkspace——
  // 切頁籤不該中止已經在燒額度的生成，而且後端要整段跑完才落盤。
  // 要停止請按畫面上的停止鍵。

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <SummaryToolbar space={space} onDraftCreated={onDraftCreated} />

      {/* 則數不合法的訊息橫跨整列，所以留在骨架這一層而不是塞進工具列裡
          ——它是「這個工作台現在不能開始」的狀態，不是某個欄位的裝飾 */}
      {limitError ? (
        <p className="shrink-0 border-b border-destructive/30 bg-destructive/10 px-5 py-1.5 text-xs text-destructive">
          {limitError}
        </p>
      ) : null}

      <SummaryOutput space={space} active={active} />
    </div>
  )
}
