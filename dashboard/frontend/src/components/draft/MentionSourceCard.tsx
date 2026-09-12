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
    <div className="shrink-0 border-b border-border px-5 py-4">
      {/*
        這是一張標準卡片（標頭帶 ＋ 內容區），與摘要工作台的產出卡同一個語法。
        改版前它是「一段浮在背景上的 meta ＋ 一個 muted 框」，與產出區的層級
        看不出關係；收進卡片之後，「這些標記描述的是這一則來源訊息」變成版面
        本身講得出來的事。
      */}
      <section className="overflow-hidden rounded-xl border border-border bg-surface">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2">
          {/* 刻意維持 span 不升成 heading：這一區在改版前後都不是標題層級的一員，
              擅自加一個 h2 會插進產出區那兩個 h3 的上方，改變整頁的標題樹。 */}
          <span className="text-sm font-semibold">{mention.space_name}</span>
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
              'rounded border px-2 py-0.5 font-medium text-2xs',
              mention.state === 'pending'
                ? 'border-signal-line bg-signal-wash text-signal'
                : 'border-line bg-muted text-provenance',
            )}
          >
            {mention.state === 'pending'
              ? '待處理'
              : mention.state === 'manual'
                ? '自選對話'
                : '✓ 已處理'}
          </span>
        </div>

        <div className="p-4">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="rounded bg-signal-wash px-1.5 py-0.5 font-medium text-verified">
              {mention.sender_display}
            </span>
            {/* 分隔用細豎線不用中點：`·` 在設計原則裡是禁用的，
                而且它與等寬數字擠在一起時很難一眼切開欄位 */}
            <span aria-hidden className="h-3 w-px bg-line-strong" />
            <span className="metric">{formatDateTime(mention.create_time)}</span>
          </div>

          {/* 這段說明原本只活在 badge 的 tooltip 屬性裡，鍵盤與觸控使用者拿不到 */}
          {mention.state === 'manual' ? (
            <p className="mt-2 text-fg-dim text-2xs leading-relaxed">
              你從摘要工作台挑的對話，不是別人 @ 你，所以不在收件匣的待辦清單裡。送出回話後會歸到「已處理」。
            </p>
          ) : null}

          {/*
            訊息內文是**凹進去的欄位**，所以底色用 `bg-background` 不是 `bg-muted`：
            muted 在淺色比 surface 暗、在深色比 surface 亮，拿它當「凹進去」兩個
            主題的方向會相反；background 在兩個主題都比 surface 暗一階。
          */}
          <p className="mt-2 rounded-lg border border-line bg-background p-3 text-xs leading-relaxed whitespace-pre-wrap">
            {mention.text ?? `（無法取回訊息內容${mention.content_error ? `：${mention.content_error}` : ''}）`}
          </p>
        </div>
      </section>
    </div>
  )
}
