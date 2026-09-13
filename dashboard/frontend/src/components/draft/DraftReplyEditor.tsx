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
    // 與〈脈絡分析〉同一個卡片語法：標題與主要動作都在標頭帶裡。
    // 送出鈕放在標頭帶右側，因為它作用的對象就是這張卡的內容。
    <section className="overflow-hidden rounded-xl border border-border bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold">
          <MessageSquareQuoteIcon className="size-4 text-verified" />
          建議回話
          <span className="text-xs font-normal text-muted-foreground">（可直接編輯）</span>
        </h3>
        <Button
          size="sm"
          onClick={onRequestSend}
          // 三個條件各有理由：串流中送出的是半截草稿；送出中連按會送兩則；
          // 空白內容送出去是一則空訊息。`DraftReplyWorkspace.test.tsx` 各有一條守著。
          disabled={streaming || sending || !replyText.trim()}
        >
          <SendIcon />
          送出回話
        </Button>
      </div>
      <div className="p-4">
        <Textarea
          value={replyText}
          onChange={(event) => setReplyText(event.target.value)}
          rows={10}
          placeholder="建議回話會串流到這裡，你可以直接修改。"
          // 這是要給人讀的中文散文，不是 log：等寬對 CJK 沒作用，只會讓它難讀。
          // 可編輯欄位是**凹進去**的，所以底色要壓到 background——元件預設是
          // 透明（深色另有 input/30），疊在卡片的 surface 上會和卡面一樣平。
          className="min-h-48 bg-background text-base leading-relaxed dark:bg-background"
        />
      </div>
    </section>
  )
}
