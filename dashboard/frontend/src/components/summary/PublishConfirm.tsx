import { useState } from 'react'
import { toast } from 'sonner'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { api, errorMessage } from '@/lib/api'
import { useSummaryStore } from '@/store/summary'
import type { Space } from '@/lib/types'

interface PublishConfirmProps {
  space: Space | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

/**
 * 推播 Summary 回 Google Chat 的二次確認（規格 5.4）。
 *
 * **這是這個工作台唯一不可撤回的動作。** 所以確認框一定要同時說出三件事：
 * 送到哪個 Space、以**本人**身分（不是機器人）、送出後 ChatPulse 無法撤回；
 * 再加上會送出什麼的**全文預覽**。少了預覽，使用者是在不知道內容的情況下
 * 按下一個不可逆的按鈕。
 *
 * `title` 是 `ConfirmDialog` 的 React prop（渲染成 DialogTitle 的文字節點），
 * 不會變成 DOM 的 tooltip 屬性——`lib/tokens.test.ts` 的 `TITLE_BUDGET`
 * 白名單就是因此才留得下這一筆，`SummaryWorkspace.test.tsx` 有一條
 * 「渲染結果裡 [title] 選得到 0 個」在證明它。
 */
export function PublishConfirm({ space, open, onOpenChange }: PublishConfirmProps) {
  const text = useSummaryStore((s) => s.text)
  const [publishing, setPublishing] = useState(false)

  const handlePublish = async () => {
    if (!space || !text.trim()) return
    setPublishing(true)
    try {
      await api.publish({ space_id: space.id, text })
      toast.success(`已以你本人身分推播回「${space.displayName}」`)
      onOpenChange(false)
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setPublishing(false)
    }
  }

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title="推播這份 Summary 回 Google Chat？"
      description={
        <>
          將以<strong className="text-foreground">你本人的身分</strong>（不是機器人）送出到 Space
          「<strong className="text-foreground">{space?.displayName ?? '—'}</strong>」。
          送出後無法在 ChatPulse 撤回。
        </>
      }
      preview={text}
      confirmLabel="確認推播"
      pending={publishing}
      onConfirm={() => void handlePublish()}
    />
  )
}
