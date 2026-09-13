import { ChevronLeftIcon } from 'lucide-react'
import { hashForMentions, hashForSummary } from '@/lib/route'
import { useRouter } from '@/router/useRouter'

/**
 * 768–1024 的主從切換的返回麵包屑（設計規格 §12）。
 *
 * 那個寬度下清單與工作區同時只顯示一個，所以工作區需要一條回得去的路。
 * **只在那個寬度出現**（`lg:hidden`）——兩欄並存時清單就在左邊，多一條麵包屑
 * 只是噪音。
 *
 * ### 為什麼「顯示哪一半」不另外寫狀態
 *
 * 規格 §12 說得對：這件事與 URL 天然對應——`#/summary` 是清單、
 * `#/summary/:key` 是工作區。所以返回就是導覽到沒有 id 的那個位置，
 * 不需要一個「現在在主還是從」的旗標。少一個狀態就少一種不同步。
 *
 * 配套的一件事在 `router/useRouteSync.ts`：導覽到沒有 id 的位置時**不**清掉
 * store 的選取。不然在收件匣按返回會連摘要工作台建立的草稿目標一起清掉，
 * 而那一則不在收件匣清單裡、找不回來。
 */
export function MasterDetailBack({ view }: { view: 'summary' | 'mentions' }) {
  const { navigate } = useRouter()

  return (
    <nav aria-label="麵包屑" className="border-border shrink-0 border-b px-4 py-1.5 lg:hidden">
      <button
        type="button"
        onClick={() => navigate(view === 'mentions' ? hashForMentions() : hashForSummary())}
        className="text-fg-subtle hover:text-foreground -ml-1.5 flex items-center gap-1 rounded px-1.5 py-1 text-xs"
      >
        <ChevronLeftIcon className="size-3.5" aria-hidden />
        {view === 'mentions' ? '返回 Mention 收件匣' : '返回 Space 清單'}
      </button>
    </nav>
  )
}
