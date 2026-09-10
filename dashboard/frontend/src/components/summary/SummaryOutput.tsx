import { useState } from 'react'
import { AlertCircleIcon, CopyIcon, Loader2Icon, SendIcon, WandSparklesIcon } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { ActionItems } from '@/components/ActionItems'
import { Markdown } from '@/components/Markdown'
import { SpaceMessagePreview } from '@/components/SpaceMessagePreview'
import { PublishConfirm } from '@/components/summary/PublishConfirm'
import { copyText } from '@/lib/clipboard'
import { cn } from '@/lib/utils'
import { providerLabel, useProviderStore } from '@/store/providers'
import { useSummaryStore } from '@/store/summary'
import type { Space } from '@/lib/types'

interface SummaryOutputProps {
  space: Space | null
  /**
   * 這個工作台目前看得見嗎。兩個工作台常駐掛載之後，看不見的那一半仍會收到
   * 每個串流 chunk；傳下去讓 Markdown 在不可見時暫停重新 parse（§7.3）。
   */
  active: boolean
}

/**
 * 摘要工作台的產出區：訊息預覽、空狀態、產出的來源標記、複製／推播、
 * Markdown 本體與待辦清單（規格 §9.2）。
 *
 * 這是唯一會被串流 chunk 帶著重繪的一半，所以工具列另外成檔——
 * 把兩者放在同一個元件裡，改一個設定值就會讓整份 Markdown 重新 parse。
 */
export function SummaryOutput({ space, active }: SummaryOutputProps) {
  const styles = useSummaryStore((s) => s.styles)
  const style = useSummaryStore((s) => s.style)
  const streaming = useSummaryStore((s) => s.streaming)
  const text = useSummaryStore((s) => s.text)
  const meta = useSummaryStore((s) => s.meta)
  const error = useSummaryStore((s) => s.error)
  const providers = useProviderStore((s) => s.providers)

  const [confirmOpen, setConfirmOpen] = useState(false)

  const styleItems = Object.fromEntries(styles.map((item) => [item.value, item.label]))

  const handleCopy = async () => {
    const ok = await copyText(text)
    if (ok) toast.success('已複製摘要 Markdown')
    else toast.error('複製失敗，請手動選取內容')
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-5">
      {error ? (
        <div className="mb-4 flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
          <AlertCircleIcon className="mt-0.5 size-3.5 shrink-0" />
          <span className="leading-relaxed">{error}</span>
        </div>
      ) : null}

      {/* 訊息預覽。選了 Space 就看得到最近幾則在講什麼，不必先跑一次摘要
          （也不必為了看一眼就燒 AI 額度）。有摘要時預設收起來讓摘要當主角。 */}
      {space ? (
        <div className="mb-4">
          <SpaceMessagePreview space={space} defaultCollapsed={Boolean(text) || streaming} />
        </div>
      ) : null}

      {!text && !streaming && !error ? (
        <div
          className={cn(
            'flex flex-col items-center justify-center gap-3 text-center',
            space ? 'py-8' : 'h-full',
          )}
        >
          <span className="flex size-12 items-center justify-center rounded-full bg-muted text-signal">
            <WandSparklesIcon className="size-5" />
          </span>
          <div>
            <p className="text-sm font-medium">
              {space
                ? '按「開始摘要」產生一份結構化 Summary'
                : '選一個 Space，產生一份結構化 Summary'}
            </p>
            <p className="mx-auto mt-1 max-w-sm text-xs text-muted-foreground">
              摘要只屬於你本人，其他 Viewer 看不到。三種風格會產生不同深度的結果：通用、技術細節、只要待辦。
            </p>
          </div>
        </div>
      ) : null}

      {text || streaming ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span className="rounded border border-signal-line bg-signal-wash px-2 py-0.5 font-medium text-signal">
                {styleItems[style] ?? style}
              </span>
              {meta ? (
                <span className="font-mono">
                  {meta.space} · 讀取 {meta.message_count} 則
                  {meta.image_count !== undefined ? ` · 圖片 ${meta.image_count} 張` : ''}
                </span>
              ) : (
                <span>正在準備…</span>
              )}
              {/* 一律以 meta 回報的供應商為準——伺服器可能因別名解析而用了別的。
                  「本次實際使用的供應商與模型」這句說明已經是證據欄 model
                  那一列的可見內容（§5.7），這裡不再掛 tooltip——badge 本身
                  顯示的就是供應商與模型（§10.3）。 */}
              {meta?.provider ? (
                <span className="rounded border border-line bg-muted px-2 py-0.5 font-medium text-provenance">
                  {providerLabel(providers, meta.provider)}
                  {meta.model ? <span className="ml-1 metric">· {meta.model}</span> : null}
                </span>
              ) : null}
              {streaming ? (
                <span className="flex items-center gap-1">
                  <Loader2Icon className="size-3 animate-spin" />
                  串流中
                </span>
              ) : null}
            </div>

            <div className="flex items-center gap-2">
              <Button size="sm" variant="outline" onClick={() => void handleCopy()} disabled={!text}>
                <CopyIcon />
                複製 Markdown
              </Button>
              <Button
                size="sm"
                onClick={() => setConfirmOpen(true)}
                disabled={!text || streaming || !space}
              >
                <SendIcon />
                推播回 Google Chat
              </Button>
            </div>
          </div>

          <Markdown
            source={text}
            typing={streaming}
            active={active}
            className="rounded-xl border border-border bg-card/60 p-5"
          />

          {!streaming && text ? <ActionItems markdown={text} /> : null}
        </div>
      ) : null}

      <PublishConfirm space={space} open={confirmOpen} onOpenChange={setConfirmOpen} />
    </div>
  )
}
