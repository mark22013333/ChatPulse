import { useMemo } from 'react'
import { AlertCircleIcon, CompassIcon } from 'lucide-react'
import { Markdown } from '@/components/Markdown'
import { DraftReplyEditor } from '@/components/draft/DraftReplyEditor'
import { formatDateTime } from '@/lib/format'
import { splitDraft, useDraftStore } from '@/store/draft'

interface DraftOutputPaneProps {
  /** 這個工作台目前看得見嗎。看不見時讓 Markdown 停止重新 parse（規格 §7.3） */
  active: boolean
  onRequestSend: () => void
}

/**
 * 草稿右欄：脈絡分析 ＋ 建議回話（規格 §9.2）。
 *
 * 改版前這裡還有一排會換行的彩色小藥丸——脈絡則數、合併幾則、圖片幾張、
 * 供應商、口氣、Persona、自訂提示、Sepia 狀態、每個 Reference Space，
 * 全部 11px、四色混雜、位置隨內容浮動。它們現在住在右側的證據欄，有固定
 * 的位置與順序（規格 §5）。**不要把它們加回來。**
 */
export function DraftOutputPane({ active, onRequestSend }: DraftOutputPaneProps) {
  const raw = useDraftStore((s) => s.raw)
  const streaming = useDraftStore((s) => s.streaming)
  const error = useDraftStore((s) => s.error)
  const polish = useDraftStore((s) => s.polish)
  const restored = useDraftStore((s) => s.restored)
  const restoredAt = useDraftStore((s) => s.restoredAt)
  // 還原的這份有沒有帶到完整證據（新格式才有）。用 context 當代表：
  // 它與 reference_spaces／answering／image_count 是同一批、同時存或同時缺。
  const hasFullEvidence = useDraftStore((s) => Boolean(s.meta?.context))

  const sections = useMemo(() => splitDraft(raw), [raw])

  return (
    <div className="min-h-0 overflow-y-auto p-5">
      {error ? (
        <div className="mb-4 flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
          <AlertCircleIcon className="mt-0.5 size-3.5 shrink-0" />
          <span className="leading-relaxed">{error}</span>
        </div>
      ) : null}

      {!raw && !streaming && !error ? (
        <p className="py-10 text-center text-xs text-muted-foreground">
          勾好 Reference Space 之後按「產生 Draft Reply」。草稿永遠只是草稿，一定要你看過、改過、確認後才會送出。
        </p>
      ) : null}

      {raw || streaming ? (
        <div className="space-y-4">
          {/*
            還原的草稿要**在內容上方**講清楚它是舊的。
            不講的話它與剛產生的長得一模一樣，而兩者差很多：這一份可能是
            好幾天前、用當時的設定與當時的對話內容產的，直接送出去會送出
            過期的回覆。放在內容上方而不是證據欄，是因為看內容的人不一定
            會展開證據欄。
          */}
          {restored ? (
            <div className="rounded border border-line-evidence bg-muted/40 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
              <span className="font-medium text-foreground">這是先前存下來的草稿</span>
              {restoredAt ? <span className="ml-1">產生於 {formatDateTime(restoredAt)}</span> : null}
              {/*
                只有**真的缺**證據時才這樣講。2026-09-11 起產生的草稿會把整份
                meta 一起存下來，證據欄是完整的——對那些還說「沒有保存」就是
                一句假話，而且會讓人白白重新產生一次（燒配額）。
                判準用 `meta.context` 在不在，因為那正是舊格式唯一缺的那一批
                欄位的代表（見 `store/draft.ts` 的 loadStored）。
              */}
              {hasFullEvidence ? (
                <span className="ml-1">證據欄是產生當下記錄的完整內容。</span>
              ) : (
                <span className="ml-1">
                  產生當下的脈絡證據沒有保存，證據欄只看得到生成、回話設定與潤稿。
                  要拿到完整證據請重新產生。
                </span>
              )}
            </div>
          ) : null}

          {/*
            潤稿被退回時要明說原因。只放一個琥珀 badge 不夠——
            使用者需要知道「是哪個事實被改動了」，那是判斷「模型在亂改」
            還是「檢查太嚴」的唯一依據。
          */}
          {polish && !polish.polished && polish.fallback_reason ? (
            <div className="mt-2 rounded border border-caution-line bg-caution/10 px-3 py-2 text-xs leading-relaxed text-caution">
              <span className="font-medium">Sepia 潤稿未採用</span>
              <span className="ml-1">{polish.fallback_reason}</span>
              <span className="ml-1 text-muted-foreground">
                下面顯示的是未潤稿的版本，內容仍然可以直接送出。
              </span>
            </div>
          ) : null}

          {/*
            標題**在標頭帶裡**，不是浮在框外。

            改版前這裡是「一行 h3 ＋ 下面一個框」，兩者之間沒有從屬關係，
            與摘要工作台的產出卡也對不起來。收進標頭帶之後，這一頁有幾個
            區塊用掃的就數得出來——那正是卡片語法存在的理由。
          */}
          <section className="overflow-hidden rounded-xl border border-border bg-surface">
            <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2">
              <h3 className="flex items-center gap-1.5 text-sm font-semibold">
                <CompassIcon className="size-4 text-signal" />
                脈絡分析
              </h3>
            </div>
            {/* 卡片外框已經負責邊框與底色，內容只要負責內距（Markdown 類用 p-5）。
                原本的 `bg-card/60` 是階層落差還只有 0.013 時的補償手法。 */}
            <Markdown
              source={sections.context}
              typing={streaming && !sections.replyStarted}
              active={active}
              className="p-5"
            />
          </section>

          <DraftReplyEditor onRequestSend={onRequestSend} />
        </div>
      ) : null}
    </div>
  )
}
