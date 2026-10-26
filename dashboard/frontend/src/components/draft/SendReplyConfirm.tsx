import { toast } from 'sonner'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { errorMessage } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { useDraftStore, selectSending } from '@/store/draft'
import { useAuthStore } from '@/store/auth'
import { useMentionsStore } from '@/store/mentions'
import type { Mention } from '@/lib/types'

interface SendReplyConfirmProps {
  mention: Mention
  open: boolean
  onOpenChange: (open: boolean) => void
}

/**
 * 送出的二次確認（規格 5.4、7.2 步驟 6、§9.2）。
 *
 * 這是整個流程裡最需要看清楚的一刻：**送出不可撤回**，而「漏回其中一則」
 * 從草稿內容本身看不出來。所以這裡要逐則列出寄件人與時間，不是只講數字
 * ——在改版之前那份清單只活在一個 tooltip 屬性裡。
 */
export function SendReplyConfirm({ mention, open, onOpenChange }: SendReplyConfirmProps) {
  const meta = useDraftStore((s) => s.meta)
  const replyText = useDraftStore((s) => s.replyText)
  const sending = useDraftStore(selectSending)
  const applyResolved = useMentionsStore((s) => s.applyResolved)
  const applyResolvedMany = useMentionsStore((s) => s.applyResolvedMany)
  const selectedNames = meta?.reference_spaces?.map((space) => space.space_name) ?? []
  const scope = meta?.project_scope
  const evidenceMode = useAuthStore((s) => s.me?.features?.reply_evidence === true) && Boolean(scope)
  const receiptId = useDraftStore((s) => s.receiptId)

  const answering = meta?.answering ?? []

  const handleSend = async () => {
    try {
      const receipt = await useDraftStore.getState().send(mention.id)
      const updated = receipt.mentions
      if (updated.length) applyResolvedMany(updated)
      else applyResolved({ ...mention, state: 'resolved', resolved_at: new Date().toISOString() })
      toast.success(
        updated.length > 1
          ? `已送出至「${mention.space_name}」，這 ${updated.length} 則都標記為已處理`
          : `已送出至「${mention.space_name}」，該則 Mention 已標記為已處理`,
      )
      if (useDraftStore.getState().active?.epoch === receipt.scopeEpoch) onOpenChange(false)
    } catch (err) {
      // **失敗時刻意不關對話框。** 關掉的話畫面回到草稿、什麼都沒變，
      // 使用者會以為送出去了。留著它，錯誤訊息與「再試一次」都還在原地。
      toast.error(`在「${mention.space_name}」送出失敗：${errorMessage(err)}`)
    }
  }

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title="送出這則回話？"
      description={
        <>
          將以<strong className="text-foreground">你本人的身分</strong>送出，並回到原討論串（
          <strong className="text-foreground">{mention.space_name}</strong>）。
          {answering.length > 1 ? (
            <>
              {' '}
              只會送出<strong className="text-foreground">這一則</strong>訊息，
              但送出後下面這 <strong className="text-foreground">{answering.length} 則</strong>
              會一起標成已處理——送出前請確認回話真的每一則都回到了。
              {/* 逐則列出來，不是只講數字。 */}
              <ul className="mt-2 space-y-0.5">
                {answering.map((item) => (
                  <li key={item.mention_id} className="flex justify-between gap-3 text-xs">
                    <span className="text-foreground">{item.sender_display ?? '未知成員'}</span>
                    <span className="metric text-muted-foreground">
                      {item.create_time ? formatDateTime(item.create_time) : ''}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <> 送出後這則 Mention 會自動變成已處理。</>
          )}
          {scope ? <span className="mt-2 block text-xs">
            {scope.profile_name} · r{scope.revision} · {evidenceMode ? '模型輔助評估，仍需確認' : '未語意查證'}<br />
            目標 {scope.target_space_id} / {scope.target_thread_name ?? '無討論串'}<br />
            本次選用 {scope.sources.filter((source) => source.use === 'reply_context').map((source) => source.label).join('、') || '無補充來源'}
          </span> : null}
          {selectedNames.length > 0 ? (
            <>
              <br />
              本次參考的 Reference Space：{selectedNames.join('、')}
            </>
          ) : null}
        </>
      }
      preview={replyText}
      previewLabel="回話全文預覽"
      confirmLabel="確認送出"
      pending={sending || (evidenceMode && !receiptId)}
      onConfirm={() => void handleSend()}
    />
  )
}
