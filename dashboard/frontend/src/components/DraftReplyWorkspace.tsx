import { useEffect, useMemo, useState } from 'react'
import {
  AlertCircleIcon,
  CompassIcon,
  Loader2Icon,
  MessageSquareQuoteIcon,
  SendIcon,
  SparklesIcon,
  SquareIcon,
  XIcon,
} from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { Markdown } from '@/components/Markdown'
import { ProviderSelect } from '@/components/ProviderSelect'
import { SpaceList } from '@/components/SpaceList'
import { errorMessage } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { splitDraft, useDraftStore } from '@/store/draft'
import { useMentionsStore } from '@/store/mentions'
import { providerLabel, useProviderStore } from '@/store/providers'
import { filterSpaces, useSpacesStore } from '@/store/spaces'
import type { DraftContextMeta, Mention } from '@/lib/types'

interface DraftReplyWorkspaceProps {
  mention: Mention | null
}

const CONTEXT_MODE_LABEL: Record<DraftContextMeta['mode'], string> = {
  flat_window: '前後脈絡',
  thread: '討論串',
  thread_thin: '討論串＋鄰近',
}

/** 把 meta.context 濃縮成一行。舊版後端沒有這個欄位，退回原本的「討論串 N 則」。 */
function contextLabel(context: DraftContextMeta | undefined, fallback: number | undefined) {
  if (!context) return `討論串 ${fallback ?? 0} 則`
  const label = CONTEXT_MODE_LABEL[context.mode] ?? '脈絡'
  const partial = context.coverage === 'partial' ? '（不連續）' : ''
  return `${label} ${context.message_count} 則${partial}`
}

/** hover 才需要看的細節：涵蓋的時間區間與各區塊的組成。 */
function contextTitle(context: DraftContextMeta | undefined) {
  if (!context) return '本次送進模型的討論串則數'
  const lines = context.blocks.map((b) => `${b.label}：${b.count} 則`)
  const { start, end } = context.time_range
  if (start && end) {
    lines.push(`涵蓋 ${formatDateTime(start)} ~ ${formatDateTime(end)}`)
  }
  if (context.coverage === 'partial') {
    lines.push('系統沒能取回這則訊息周圍的完整對話（它可能太舊了），脈絡不保證連續')
  }
  return lines.join('\n')
}

/**
 * Draft Reply 工作區（規格 7 節）。
 * Reference Space 預設一個都不勾（7.3），送出前一定要二次確認（7.2 步驟 6）。
 */
export function DraftReplyWorkspace({ mention }: DraftReplyWorkspaceProps) {
  const spaces = useSpacesStore((state) => state.items)
  const applyResolved = useMentionsStore((state) => state.applyResolved)
  const providers = useProviderStore((state) => state.providers)

  const {
    referenceSpaceIds,
    referenceSearch,
    refLimit,
    refLimitError,
    streaming,
    raw,
    meta,
    error,
    replyText,
    sending,
    toggleReference,
    clearReferences,
    setReferenceSearch,
    setRefLimit,
    setReplyText,
    generate,
    abort,
    reset,
  } = useDraftStore()

  const [confirmOpen, setConfirmOpen] = useState(false)

  // store 記著「目前這份草稿是誰的」，用它判斷要不要清空，元件自己不必追蹤
  const streamedMentionId = useDraftStore((state) => state.mentionId)

  // 只有**真的換了一則 Mention** 才清空。
  //
  // 以前這裡是無條件 reset()，而 App.tsx 的頁籤是條件渲染（不是隱藏），
  // 切頁籤會把這個元件整個卸載重掛——於是每次切回來，掛載時的 reset()
  // 就把還在串流的草稿清光了，使用者什麼都看不到。
  useEffect(() => {
    const id = mention?.id ?? null
    if (id !== null && streamedMentionId !== null && id !== streamedMentionId) {
      reset()
    }
  }, [mention?.id, streamedMentionId, reset])

  // 這裡刻意**不**在卸載時 abort。
  //
  // 串流狀態全部住在 store，元件只是畫面；卸載就中止等於「切個頁籤就把
  // 已經燒掉的 AI 額度丟掉」，而且後端要整段跑完才落盤（server.py 的
  // create_draft），內容會一起消失。要停止請按畫面上的停止鍵——那才是
  // 使用者明確表達的意圖。

  const sections = useMemo(() => splitDraft(raw), [raw])
  const referenceCandidates = useMemo(
    () => filterSpaces(spaces, referenceSearch),
    [spaces, referenceSearch],
  )
  const selectedNames = useMemo(
    () =>
      referenceSpaceIds
        .map((id) => spaces.find((space) => space.id === id)?.displayName ?? id)
        .filter(Boolean),
    [referenceSpaceIds, spaces],
  )

  if (!mention) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
        <span className="flex size-12 items-center justify-center rounded-full bg-muted text-sky-500">
          <MessageSquareQuoteIcon className="size-5" />
        </span>
        <div>
          <p className="text-sm font-medium">從左側收件匣點一則 Mention</p>
          <p className="mx-auto mt-1 max-w-sm text-xs text-muted-foreground">
            系統會取回該討論串的完整對話，你可以再勾選其他 Space 當作 Reference Space
            補充脈絡——被 @ 的問題，答案通常不在提問的那個 Space 裡。
          </p>
        </div>
      </div>
    )
  }

  const handleSend = async () => {
    try {
      const updated = await useDraftStore.getState().send(mention.id)
      if (updated) applyResolved(updated)
      else applyResolved({ ...mention, state: 'resolved', resolved_at: new Date().toISOString() })
      toast.success('已送出回話，該則 Mention 已標記為已處理')
      setConfirmOpen(false)
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 原始 Mention */}
      <div className="shrink-0 border-b border-border px-5 py-3">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="text-sm font-semibold">{mention.space_name}</span>
          <span className="text-xs text-muted-foreground">
            {mention.sender_display} · {formatDateTime(mention.create_time)}
          </span>
          {/* manual 必須單獨標。以前這裡只分 pending 與「其他」，於是從摘要
              工作台挑來的草稿目標被顯示成「已處理」——但它不在「已處理」清單裡
              （那個分頁查的是 resolved），使用者會以為系統漏掉了他的紀錄。 */}
          <span
            className={cn(
              'rounded border px-1.5 py-0.5 text-[10px]',
              mention.state === 'pending' && 'border-sky-500/30 bg-sky-500/10 text-sky-500',
              mention.state === 'resolved' &&
                'border-emerald-500/30 bg-emerald-500/10 text-emerald-500',
              mention.state === 'manual' && 'border-border bg-muted text-muted-foreground',
            )}
            title={
              mention.state === 'manual'
                ? '你從摘要工作台挑的對話，不是別人 @ 你，所以不在收件匣的待辦清單裡。送出回話後會歸到「已處理」。'
                : undefined
            }
          >
            {mention.state === 'pending'
              ? '待處理'
              : mention.state === 'manual'
                ? '手動指定'
                : '已處理'}
          </span>
        </div>
        <p className="mt-2 rounded-lg border border-border bg-muted/40 p-3 text-xs leading-relaxed whitespace-pre-wrap">
          {mention.text ?? `（無法取回訊息內容${mention.content_error ? `：${mention.content_error}` : ''}）`}
        </p>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[280px_1fr]">
        {/* Reference Space 勾選 */}
        <div className="flex min-h-0 flex-col border-b border-border xl:border-r xl:border-b-0">
          <div className="shrink-0 space-y-2 px-3 py-2.5">
            <div className="flex items-center gap-2">
              <h3 className="text-xs font-semibold">Reference Space</h3>
              <span className="text-[10px] text-muted-foreground">
                已勾選 {referenceSpaceIds.length}
              </span>
              {referenceSpaceIds.length > 0 ? (
                <Button size="xs" variant="ghost" className="ml-auto" onClick={clearReferences}>
                  <XIcon />
                  清空
                </Button>
              ) : null}
            </div>
            <p className="text-[10px] leading-relaxed text-muted-foreground">
              預設一個都不勾。勾選的 Space 近期訊息會一併送進脈絡。
            </p>
            <Input
              value={referenceSearch}
              onChange={(event) => setReferenceSearch(event.target.value)}
              placeholder="搜尋 Space 名稱…"
              className="h-7"
            />
            <div className="flex items-end gap-2">
              <div className="flex-1 space-y-1">
                <Label htmlFor="draft-limit" className="text-[10px] text-muted-foreground">
                  每群抓取則數（1~1000）
                </Label>
                <Input
                  id="draft-limit"
                  type="number"
                  min={1}
                  max={1000}
                  value={Number.isNaN(refLimit) ? '' : refLimit}
                  onChange={(event) => setRefLimit(event.target.value)}
                  className="h-7"
                  aria-invalid={Boolean(refLimitError)}
                />
              </div>
            </div>
            {refLimitError ? (
              <p className="text-[10px] text-destructive">{refLimitError}</p>
            ) : null}

            <ProviderSelect id="draft-provider" disabled={streaming} triggerClassName="w-full" />
          </div>

          <SpaceList
            spaces={referenceCandidates}
            checkedIds={referenceSpaceIds}
            onToggle={(space) => toggleReference(space.id)}
            emptyHint="查無符合的 Space"
            className="max-h-64 xl:max-h-none"
          />

          <div className="shrink-0 border-t border-border p-2.5">
            {streaming ? (
              <Button variant="outline" className="w-full" onClick={abort}>
                <SquareIcon />
                停止串流
              </Button>
            ) : (
              <Button
                className="w-full"
                onClick={() => void generate(mention.id)}
                disabled={Boolean(refLimitError)}
              >
                <SparklesIcon />
                {raw ? '重新產生 Draft Reply' : '產生 Draft Reply'}
              </Button>
            )}
          </div>
        </div>

        {/* 產出：脈絡分析 + 建議回話 */}
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
              {meta ? (
                <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                  {/* 脈絡的「形狀」一定要顯示：私訊走前後窗、群組走討論串，
                      涵蓋範圍差很多，而從草稿內容完全看不出來是哪一種。
                      在此之前這裡只寫「討論串 N 則」，私訊永遠顯示 1 則也沒人看得懂為什麼。 */}
                  <span
                    className={cn(
                      'font-mono',
                      meta.context?.coverage === 'partial' && 'text-amber-600 dark:text-amber-400',
                    )}
                    title={contextTitle(meta.context)}
                  >
                    {contextLabel(meta.context, meta.thread_message_count)}
                  </span>
                  {/* 圖片張數一定要顯示：附件有沒有被讀進去，從草稿內容看不出來，
                      使用者只能猜。顯示 0 張也有意義——那代表「讀了但沒有圖」。 */}
                  {meta.image_count !== undefined ? (
                    <span
                      className="font-mono"
                      title={
                        meta.images_skipped?.length
                          ? `略過：${meta.images_skipped.join('、')}`
                          : '實際送進模型的圖片張數'
                      }
                    >
                      · 圖片 {meta.image_count} 張
                      {meta.images_skipped?.length ? `（略過 ${meta.images_skipped.length}）` : ''}
                    </span>
                  ) : null}
                  {/* 一律以 meta 回報的供應商為準——伺服器可能因別名解析而用了別的 */}
                  {meta.provider ? (
                    <span
                      className="rounded border border-violet-500/25 bg-violet-500/10 px-2 py-0.5 font-medium text-violet-600 dark:text-violet-400"
                      title="本次實際使用的 AI 供應商與模型（來自 meta 事件）"
                    >
                      {providerLabel(providers, meta.provider)}
                      {meta.model ? <span className="ml-1 font-mono">· {meta.model}</span> : null}
                    </span>
                  ) : null}
                  {(meta.reference_spaces ?? []).map((ref) => (
                    <span
                      key={ref.space_id}
                      className="rounded border border-border bg-muted/50 px-1.5 py-0.5"
                    >
                      {ref.space_name} · {ref.message_count} 則
                    </span>
                  ))}
                  {streaming ? (
                    <span className="flex items-center gap-1">
                      <Loader2Icon className="size-3 animate-spin" />
                      串流中
                    </span>
                  ) : null}
                </div>
              ) : null}

              <section>
                <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold">
                  <CompassIcon className="size-4 text-sky-500" />
                  脈絡分析
                </h3>
                <Markdown
                  source={sections.context}
                  typing={streaming && !sections.replyStarted}
                  className="rounded-xl border border-border bg-card/60 p-4"
                />
              </section>

              <section>
                <div className="mb-2 flex items-center gap-1.5">
                  <MessageSquareQuoteIcon className="size-4 text-emerald-500" />
                  <h3 className="text-sm font-semibold">建議回話</h3>
                  <span className="text-[11px] text-muted-foreground">（可直接編輯）</span>
                  <Button
                    size="sm"
                    className="ml-auto"
                    onClick={() => setConfirmOpen(true)}
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
                  className="min-h-48 font-mono text-xs leading-relaxed"
                />
              </section>
            </div>
          ) : null}
        </div>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="送出這則回話？"
        description={
          <>
            將以<strong className="text-foreground">你本人的身分</strong>送出，並回到原討論串（
            <strong className="text-foreground">{mention.space_name}</strong>）。
            送出後這則 Mention 會自動變成已處理。
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
        pending={sending}
        onConfirm={() => void handleSend()}
      />
    </div>
  )
}
