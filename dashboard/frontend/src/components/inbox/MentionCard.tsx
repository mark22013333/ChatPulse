import { CheckCheckIcon, UndoIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { relativeTime } from '@/lib/format'
import { type MergeBlockReason } from '@/lib/merge'
import { MERGE_BLOCK_LABEL } from '@/lib/mergeCopy'
import { cn } from '@/lib/utils'
import { isOutstanding } from '@/store/mentions'
import type { Mention, MentionState } from '@/lib/types'

interface MentionCardProps {
  mention: Mention
  /** 方向鍵導航的定位點；由清單那一層給，卡片不知道自己排第幾。 */
  index: number
  /** 這一則是目前選中的那則嗎 */
  active: boolean
  /** 勾起來要一起回 */
  checked: boolean
  /** 這一頁提供合併勾選嗎（已處理那一頁不提供） */
  mergeable: boolean
  /** 現在能不能勾。false 時下面會說出原因。 */
  selectable: boolean
  /** 不能勾的原因。`null` 表示可以勾。 */
  blocked: MergeBlockReason | null
  /** 已經勾了東西、而這一則不能一起勾——整張卡片淡下去 */
  dimmed: boolean
  onSelect: () => void
  onToggleMerge: () => void
  onToggleState: (next: MentionState) => void
}

/**
 * 收件匣裡的一則 Mention。
 *
 * 刻意做成純顯示元件（狀態全由清單那一層算好傳進來）：合併能不能勾要看
 * **目前已經勾了哪些**與那些 Space 的關係（`mergeBlockReason`），那份上下文
 * 只有清單那一層有。卡片自己去 store 撈的話會各自算一份，兩份必然漂移。
 */
export function MentionCard({
  mention,
  index,
  active,
  checked,
  mergeable,
  selectable,
  blocked,
  dimmed,
  onSelect,
  onToggleMerge,
  onToggleState,
}: MentionCardProps) {
  return (
    <div
      className={cn(
        'rounded-lg border p-2.5 transition-colors',
        // 勾選與選中都是 signal 底，靠邊框強弱分辨：勾選用實心 signal，
        // 選中用半透明的 signal-line——兩者都換成同一組 token 會讓狀態糊在一起
        checked
          ? 'border-signal bg-signal-wash'
          : active
            ? 'border-signal-line bg-signal-wash'
            : 'border-border/70 bg-card/50 hover:border-border',
        dimmed && 'opacity-45',
      )}
    >
      <div className="flex items-start gap-2">
        {mergeable ? (
          <Checkbox
            checked={checked}
            disabled={!selectable}
            onCheckedChange={onToggleMerge}
            className="mt-0.5 shrink-0"
            aria-label={`選取來自 ${mention.sender_display} 的這則一起回`}
            // 不可勾選的原因改成畫面上讀得到的一行（見下方），
            // 用 aria-describedby 綁過去。原本它只活在 title 屬性裡，
            // 鍵盤與觸控使用者完全拿不到（設計規格 §10.3）。
            aria-describedby={!selectable && blocked ? `merge-block-${mention.id}` : undefined}
          />
        ) : null}
        <button
          type="button"
          // 方向鍵導航的定位點（見 MentionInbox 的 onListKeyDown）。放在這顆
          // 按鈕上而不是外層 li：從勾選框按 ↓ 不該跳到下一則
          data-mention-index={index}
          className="min-w-0 flex-1 text-left"
          onClick={onSelect}
        >
          <div className="flex items-baseline justify-between gap-2">
            <span className="truncate text-xs font-medium">{mention.space_name}</span>
            <span className="shrink-0 text-2xs text-muted-foreground">
              {relativeTime(mention.create_time)}
            </span>
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">{mention.sender_display} 提到你</p>
          <p className="mt-1 line-clamp-3 text-xs leading-relaxed whitespace-pre-wrap">
            {mention.text
              ? mention.text
              : mention.content_error
                ? `（無法取回訊息內容：${mention.content_error}）`
                : '（訊息內容取不到）'}
          </p>
          {/* 不能勾的要說原因。只把它變灰的話，使用者只會覺得壞了。
              這行是決定「能不能送」的資訊，字級維持 13px 不縮到 12px。

              顯示條件是 `!selectable`（不是「已經勾了東西時才說」）：
              使用者第一次想勾就被擋住的那一刻，正是最需要知道為什麼的時候。 */}
          {!selectable && blocked ? (
            <p id={`merge-block-${mention.id}`} className="mt-1 text-xs text-caution">
              {MERGE_BLOCK_LABEL[blocked]}
            </p>
          ) : null}
        </button>
      </div>

      <div className="mt-2 flex justify-end">
        {/* 判準用 store 的 isOutstanding，不要在這裡另寫一次：
            manual（摘要工作台挑的草稿目標）也是待處理，寫成
            `=== 'pending'` 會讓它顯示「退回待處理」，而按下去會
            把 state 改成 pending、無聲抹掉「自選對話」這個來源標記 */}
        {isOutstanding(mention.state) ? (
          <Button size="xs" variant="ghost" onClick={() => onToggleState('resolved')}>
            <CheckCheckIcon />
            標記已處理
          </Button>
        ) : (
          <Button size="xs" variant="ghost" onClick={() => onToggleState('pending')}>
            <UndoIcon />
            退回待處理
          </Button>
        )}
      </div>
    </div>
  )
}
