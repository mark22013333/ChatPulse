import { cn } from '@/lib/utils'
import { formatDateTime } from '@/lib/format'
import type { Mention } from '@/lib/types'

/**
 * 原始 Mention 卡：誰在哪個 Space 什麼時候說了什麼（規格 §9.2）。
 *
 * 純顯示，唯一的輸入是 `mention` —— 規格 §9.3 說子元件不接大 props 物件、
 * 各自用細 selector 讀 store，而 `mention` 與純顯示用的資料是那條規則明列
 * 的兩個例外。
 */
export function MentionSourceCard({ mention }: { mention: Mention }) {
  return (
    <div className="shrink-0 border-b border-border px-5 py-3">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-sm font-semibold">{mention.space_name}</span>
        <span className="text-xs text-muted-foreground">
          {mention.sender_display} · {formatDateTime(mention.create_time)}
        </span>
        {/* manual 必須單獨標。以前這裡只分 pending 與「其他」，於是從摘要
            工作台挑來的草稿目標被顯示成「已處理」——但它不在「已處理」清單裡
            （那個分頁查的是 resolved），使用者會以為系統漏掉了他的紀錄。 */}
        {/*
          三種狀態不能只靠顏色分辨（設計原則 3）。`--verified` 與 `--signal`
          刻意是同一個色，所以這裡改成：**只有需要動作的「待處理」帶訊號色**，
          另外兩種安靜下來，再用 ✓ 與文字把「已處理」和「自選對話」分開。

          這一處刻意**不用** store 的 isOutstanding()：那個判準回答「還沒處理
          完嗎」（二分），這裡要的是三分。改判準時不要把它一起改掉。
        */}
        <span
          className={cn(
            'rounded border px-1.5 py-0.5 text-2xs',
            mention.state === 'pending'
              ? 'border-signal-line bg-signal-wash text-signal'
              : 'border-border bg-muted text-fg-dim',
          )}
        >
          {mention.state === 'pending'
            ? '待處理'
            : mention.state === 'manual'
              ? '自選對話'
              : '✓ 已處理'}
        </span>
        {/* 這段說明原本只活在 badge 的 tooltip 屬性裡，鍵盤與觸控使用者拿不到 */}
        {mention.state === 'manual' ? (
          <span className="text-fg-dim text-2xs">
            你從摘要工作台挑的對話，不是別人 @ 你，所以不在收件匣的待辦清單裡。送出回話後會歸到「已處理」。
          </span>
        ) : null}
      </div>
      <p className="mt-2 rounded-lg border border-border bg-muted/40 p-3 text-xs leading-relaxed whitespace-pre-wrap">
        {mention.text ?? `（無法取回訊息內容${mention.content_error ? `：${mention.content_error}` : ''}）`}
      </p>
    </div>
  )
}
