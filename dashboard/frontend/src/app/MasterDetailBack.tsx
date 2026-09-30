import { ChevronLeftIcon } from 'lucide-react'
import { TABLE_WORKBENCH } from '@/app/workbenchLayout'
import type { WorkbenchView } from '@/lib/modules'
import { hashForMentions, hashForSummary, hashForWorklog } from '@/lib/route'
import { cn } from '@/lib/utils'
import { useRouter } from '@/router/useRouter'

/**
 * 主從切換的返回列（設計規格 §12；2026-10-01 改版 §4.1）。
 *
 * 清單與工作區同時只顯示一個的時候，工作區需要一條回得去的路。什麼時候是
 * 「同時只顯示一個」由 `TABLE_WORKBENCH` 決定：
 *
 * - **表格型**（摘要工作台）：表格獨占整頁、點一列整頁切到詳情，**所有寬度**
 *   都是主從切換，所以返回列在所有寬度都出現。
 * - **對話型**（Mention 收件匣）：只有 768–1024 是主從切換，≥1024 清單就在
 *   左邊，多一條麵包屑只是噪音，所以仍然 `lg:hidden`。
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
/**
 * 每個工作台「回去」是回到哪裡、按鈕上要寫什麼。
 *
 * 用查表而不是 `view === 'mentions' ? A : B`：那種寫法在加第三個工作台時
 * 不會編譯失敗，只會讓新工作台的返回鍵**默默把人送回 Space 清單**。
 * `Record<WorkbenchView, …>` 漏掉任何一個都是編譯錯誤。
 */
const BACK: Record<WorkbenchView, { hash: () => string; label: string }> = {
  summary: { hash: () => hashForSummary(), label: '返回 Space 清單' },
  mentions: { hash: () => hashForMentions(), label: '返回 Mention 收件匣' },
  worklog: { hash: () => hashForWorklog(), label: '返回週檢視' },
}

export function MasterDetailBack({ view }: { view: WorkbenchView }) {
  const { navigate } = useRouter()
  const back = BACK[view]

  return (
    <nav
      aria-label="麵包屑"
      className={cn('border-border shrink-0 border-b px-4 py-1.5', !TABLE_WORKBENCH[view] && 'lg:hidden')}
    >
      <button
        type="button"
        onClick={() => navigate(back.hash())}
        className="text-fg-subtle hover:text-foreground -ml-1.5 flex items-center gap-1 rounded px-1.5 py-1 text-xs"
      >
        <ChevronLeftIcon className="size-3.5" aria-hidden />
        {back.label}
      </button>
    </nav>
  )
}
