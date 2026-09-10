import { MessageSquareQuoteIcon, SendIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useDraftStore } from '@/store/draft'

/**
 * 建議回話的行內編輯器 ＋ 送出鈕（規格 §9.2）。
 *
 * 送出鈕只負責**打開確認框**，不直接送。送出不可撤回，所以中間一定要有
 * 一步讓人看到「會送出什麼、會結掉哪幾則」（規格 7.2 步驟 6）。
 */
export function DraftReplyEditor({ onRequestSend }: { onRequestSend: () => void }) {
  const replyText = useDraftStore((s) => s.replyText)
  const setReplyText = useDraftStore((s) => s.setReplyText)
  const streaming = useDraftStore((s) => s.streaming)
  const sending = useDraftStore((s) => s.sending)

  return (
    <section>
      <div className="mb-2 flex items-center gap-1.5">
        <MessageSquareQuoteIcon className="size-4 text-verified" />
        <h3 className="text-sm font-semibold">建議回話</h3>
        <span className="text-xs text-muted-foreground">（可直接編輯）</span>
        <Button
          size="sm"
          className="ml-auto"
          onClick={onRequestSend}
          // 三個條件各有理由：串流中送出的是半截草稿；送出中連按會送兩則；
          // 空白內容送出去是一則空訊息。`DraftReplyWorkspace.test.tsx` 各有一條守著。
          disabled={streaming || sending || !replyText.trim()}
        >
          <SendIcon />
          送出回話
        </Button>
      </div>
      <Textarea
        value={replyText}
        onChange={(event) => setReplyText(event.target.value)}
        rows={10}
        placeholder="建議回話會串流到這裡，你可以直接修改。"
        // 這是要給人讀的中文散文，不是 log：等寬對 CJK 沒作用，只會讓它難讀
        className="min-h-48 text-base leading-relaxed"
      />
    </section>
  )
}
