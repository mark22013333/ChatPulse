import { useEffect, useState } from 'react'
import {
  AlertCircleIcon,
  CopyIcon,
  Loader2Icon,
  SendIcon,
  SparklesIcon,
  SquareIcon,
  WandSparklesIcon,
} from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ActionItems } from '@/components/ActionItems'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { Markdown } from '@/components/Markdown'
import { ProviderSelect } from '@/components/ProviderSelect'
import { api, errorMessage } from '@/lib/api'
import { copyText } from '@/lib/clipboard'
import { providerLabel, useProviderStore } from '@/store/providers'
import { useSummaryStore } from '@/store/summary'
import type { Space, SummaryStyleValue } from '@/lib/types'

interface SummaryWorkspaceProps {
  space: Space | null
}

export function SummaryWorkspace({ space }: SummaryWorkspaceProps) {
  const {
    styles,
    style,
    limit,
    limitError,
    streaming,
    text,
    meta,
    error,
    setStyle,
    setLimit,
    start,
    abort,
    reset,
  } = useSummaryStore()

  const providers = useProviderStore((state) => state.providers)

  const [confirmOpen, setConfirmOpen] = useState(false)
  const [publishing, setPublishing] = useState(false)

  // store 記著「目前這份摘要是哪個 Space 的」，用它判斷要不要清空
  const streamedSpaceId = useSummaryStore((state) => state.streamedSpaceId)

  // 只有**真的換了 Space** 才清空。
  //
  // 以前這裡是無條件 reset()，而 App.tsx 的頁籤是條件渲染（不是隱藏），
  // 切頁籤會把這個元件整個卸載重掛——掛載時的 reset() 就把還在串流的
  // 摘要清光了。原註解擔心的 setState-after-unmount 不會發生：狀態在
  // zustand store 裡，不是元件狀態。
  useEffect(() => {
    const id = space?.id ?? null
    if (id !== null && streamedSpaceId !== null && id !== streamedSpaceId) {
      reset()
    }
  }, [space?.id, streamedSpaceId, reset])

  // 這裡刻意**不**在卸載時 abort：理由同 DraftReplyWorkspace——
  // 切頁籤不該中止已經在燒額度的生成，而且後端要整段跑完才落盤。
  // 要停止請按畫面上的停止鍵。

  const canStart = Boolean(space) && !streaming && !limitError

  const styleItems = Object.fromEntries(styles.map((item) => [item.value, item.label]))

  const handlePublish = async () => {
    if (!space || !text.trim()) return
    setPublishing(true)
    try {
      await api.publish({ space_id: space.id, text })
      toast.success(`已以你本人身分推播回「${space.displayName}」`)
      setConfirmOpen(false)
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setPublishing(false)
    }
  }

  const handleCopy = async () => {
    const ok = await copyText(text)
    if (ok) toast.success('已複製摘要 Markdown')
    else toast.error('複製失敗，請手動選取內容')
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 工具列：目標 Space、風格、抓取則數、開始摘要 */}
      <div className="flex shrink-0 flex-wrap items-end gap-3 border-b border-border px-5 py-3">
        <div className="mr-auto min-w-0">
          <p className="truncate text-sm font-semibold">
            {space ? space.displayName : '尚未選擇 Space'}
          </p>
          <p className="truncate font-mono text-[11px] text-muted-foreground">
            {space ? space.id : '請從左側清單選一個 Space'}
          </p>
        </div>

        <div className="flex flex-col gap-1">
          <Label htmlFor="summary-style" className="text-[11px] text-muted-foreground">
            摘要風格
          </Label>
          <Select
            items={styleItems}
            value={style}
            onValueChange={(value) => setStyle(value as SummaryStyleValue)}
          >
            <SelectTrigger id="summary-style" size="sm" className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {styles.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <ProviderSelect id="summary-provider" disabled={streaming} />

        <div className="flex flex-col gap-1">
          <Label htmlFor="summary-limit" className="text-[11px] text-muted-foreground">
            抓取則數（1~1000）
          </Label>
          <Input
            id="summary-limit"
            type="number"
            min={1}
            max={1000}
            value={Number.isNaN(limit) ? '' : limit}
            onChange={(event) => setLimit(event.target.value)}
            className="h-7 w-28"
            aria-invalid={Boolean(limitError)}
          />
        </div>

        {streaming ? (
          <Button variant="outline" onClick={abort}>
            <SquareIcon />
            停止串流
          </Button>
        ) : (
          <Button disabled={!canStart} onClick={() => space && void start(space.id)}>
            <SparklesIcon />
            {text ? '重新摘要' : '開始摘要'}
          </Button>
        )}
      </div>

      {limitError ? (
        <p className="shrink-0 border-b border-destructive/30 bg-destructive/10 px-5 py-1.5 text-xs text-destructive">
          {limitError}
        </p>
      ) : null}

      {/* 輸出區 */}
      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        {error ? (
          <div className="mb-4 flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
            <AlertCircleIcon className="mt-0.5 size-3.5 shrink-0" />
            <span className="leading-relaxed">{error}</span>
          </div>
        ) : null}

        {!text && !streaming && !error ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <span className="flex size-12 items-center justify-center rounded-full bg-muted text-sky-500">
              <WandSparklesIcon className="size-5" />
            </span>
            <div>
              <p className="text-sm font-medium">選一個 Space，產生一份結構化 Summary</p>
              <p className="mx-auto mt-1 max-w-sm text-xs text-muted-foreground">
                摘要只屬於你本人，其他 Viewer 看不到。三種風格會產生不同深度的結果：通用、技術細節、只要待辦。
              </p>
            </div>
          </div>
        ) : null}

        {text || streaming ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                <span className="rounded border border-sky-500/25 bg-sky-500/10 px-2 py-0.5 font-medium text-sky-500">
                  {styleItems[style] ?? style}
                </span>
                {meta ? (
                  <span className="font-mono">
                    {meta.space} · 讀取 {meta.message_count} 則
                  </span>
                ) : (
                  <span>正在準備…</span>
                )}
                {/* 一律以 meta 回報的供應商為準——伺服器可能因別名解析而用了別的 */}
                {meta?.provider ? (
                  <span
                    className="rounded border border-violet-500/25 bg-violet-500/10 px-2 py-0.5 font-medium text-violet-600 dark:text-violet-400"
                    title={`本次實際使用的 AI 供應商與模型（來自 meta 事件）`}
                  >
                    {providerLabel(providers, meta.provider)}
                    {meta.model ? <span className="ml-1 font-mono">· {meta.model}</span> : null}
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
              className="rounded-xl border border-border bg-card/60 p-5"
            />

            {!streaming && text ? <ActionItems markdown={text} /> : null}
          </div>
        ) : null}
      </div>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
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
    </div>
  )
}
