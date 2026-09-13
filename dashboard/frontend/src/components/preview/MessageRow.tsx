import { ImageIcon } from 'lucide-react'
import type { ChatMessage } from '@/lib/types'

/**
 * 預覽面板裡的一則訊息。
 *
 * `data-message-name` 不是裝飾：它是「同一則不會出現兩次」這件事的量測點。
 * 收合前後都只該有一個——重複顯示正是這個面板最早的缺陷（外層把整串每一則
 * 印出來、點開又印一次）。`SpaceMessagePreview.test.tsx` 與
 * `tests/e2e/test_message_preview.cjs` 都靠它。
 */
export function MessageRow({ message }: { message: ChatMessage }) {
  return (
    <div data-message-name={message.name} className="rounded bg-background/40 px-2 py-1">
      <div className="flex items-baseline gap-2">
        <span className="truncate text-xs font-medium">{message.sender}</span>
        <span className="metric shrink-0 text-2xs text-muted-foreground">{message.time}</span>
      </div>
      {message.text ? (
        <p className="mt-0.5 text-xs leading-relaxed whitespace-pre-wrap">{message.text}</p>
      ) : null}
      {/* 只有圖、沒有文字的訊息以前在這個端點會整則消失。附件一定要看得見，
          否則使用者會覺得「我要 20 則怎麼只有 17 則」而找不到原因。 */}
      {message.attachment_note ? (
        <p className="mt-0.5 flex items-start gap-1 text-2xs text-muted-foreground">
          <ImageIcon className="mt-0.5 size-3 shrink-0" />
          <span className="min-w-0 break-all">{message.attachment_note}</span>
        </p>
      ) : null}
    </div>
  )
}
