import { useEffect, useMemo } from 'react'
import { AlertCircleIcon, CheckCheckIcon, InboxIcon, Loader2Icon, RefreshCwIcon, UndoIcon } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'
import { errorMessage } from '@/lib/api'
import { relativeTime } from '@/lib/format'
import { selectMentionsByState, useMentionsStore } from '@/store/mentions'
import type { MentionState } from '@/lib/types'

/** 自動重新拉取間隔（規格 6.3 的輪詢節奏對齊）。 */
const AUTO_RELOAD_MS = 45_000

interface MentionInboxProps {
  onSelect: (mentionId: number) => void
}

export function MentionInbox({ onSelect }: MentionInboxProps) {
  const items = useMentionsStore((state) => state.items)
  const counts = useMentionsStore((state) => state.counts)
  const tab = useMentionsStore((state) => state.tab)
  const loading = useMentionsStore((state) => state.loading)
  const checking = useMentionsStore((state) => state.checking)
  const error = useMentionsStore((state) => state.error)
  const selectedId = useMentionsStore((state) => state.selectedId)
  const setTab = useMentionsStore((state) => state.setTab)
  const load = useMentionsStore((state) => state.load)
  const checkNow = useMentionsStore((state) => state.checkNow)
  const setMentionState = useMentionsStore((state) => state.setMentionState)

  // 首次載入 + 每 45 秒自動重新拉取清單
  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load({ silent: true }), AUTO_RELOAD_MS)
    return () => window.clearInterval(timer)
  }, [load])

  const visible = useMemo(() => selectMentionsByState(items, tab), [items, tab])

  const handleCheckNow = async () => {
    const stats = await checkNow()
    if (stats) {
      toast.success(
        `已完成一輪採集：新增 ${stats.new_mentions ?? 0} 則，掃描 ${stats.spaces_polled ?? 0}/${stats.spaces_total ?? 0} 個 Space`,
      )
    }
  }

  const handleToggleState = async (id: number, next: MentionState) => {
    try {
      await setMentionState(id, next)
      toast.success(next === 'resolved' ? '已標記為已處理' : '已退回待處理')
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 space-y-2 border-b border-border px-3 py-2.5">
        <div className="flex items-center gap-2">
          <InboxIcon className="size-3.5 text-muted-foreground" />
          <h2 className="text-xs font-semibold tracking-wide">Mention 收件匣</h2>
          <Button
            size="xs"
            variant="ghost"
            className="ml-auto"
            onClick={() => void handleCheckNow()}
            disabled={checking}
          >
            {checking ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />}
            立即檢查
          </Button>
        </div>

        <Tabs value={tab} onValueChange={(value) => setTab(value as MentionState)}>
          <TabsList className="w-full">
            <TabsTrigger value="pending">
              待處理
              {counts.pending > 0 ? (
                <span className="ml-1 inline-flex min-w-4 items-center justify-center rounded-full bg-sky-500 px-1 text-[10px] font-semibold text-white">
                  {counts.pending}
                </span>
              ) : null}
            </TabsTrigger>
            <TabsTrigger value="resolved">
              已處理
              <span className="ml-1 text-[10px] text-muted-foreground">{counts.resolved}</span>
            </TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {error ? (
        <div className="flex shrink-0 items-start gap-2 border-b border-destructive/30 bg-destructive/10 px-3 py-2 text-[11px] text-destructive">
          <AlertCircleIcon className="mt-0.5 size-3 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {loading && items.length === 0 ? (
          <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
            <Loader2Icon className="size-3.5 animate-spin" />
            正在載入…
          </div>
        ) : null}

        {!loading && visible.length === 0 ? (
          <p className="px-2 py-8 text-center text-xs text-muted-foreground">
            {tab === 'pending' ? '沒有待處理的 Mention。' : '還沒有已處理的 Mention。'}
          </p>
        ) : null}

        <ul className="space-y-1.5">
          {visible.map((mention) => {
            const active = selectedId === mention.id
            return (
              <li key={mention.id}>
                <div
                  className={cn(
                    'rounded-lg border p-2.5 transition-colors',
                    active
                      ? 'border-sky-500/40 bg-sky-500/10'
                      : 'border-border/70 bg-card/50 hover:border-border',
                  )}
                >
                  <button
                    type="button"
                    className="w-full text-left"
                    onClick={() => onSelect(mention.id)}
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate text-xs font-medium">{mention.space_name}</span>
                      <span className="shrink-0 text-[10px] text-muted-foreground">
                        {relativeTime(mention.create_time)}
                      </span>
                    </div>
                    <p className="mt-0.5 text-[11px] text-muted-foreground">
                      {mention.sender_display} 提到你
                    </p>
                    <p className="mt-1 line-clamp-3 text-[11px] leading-relaxed whitespace-pre-wrap">
                      {mention.text
                        ? mention.text
                        : mention.content_error
                          ? `（無法取回訊息內容：${mention.content_error}）`
                          : '（訊息內容取不到）'}
                    </p>
                  </button>

                  <div className="mt-2 flex justify-end">
                    {mention.state === 'pending' ? (
                      <Button
                        size="xs"
                        variant="ghost"
                        onClick={() => void handleToggleState(mention.id, 'resolved')}
                      >
                        <CheckCheckIcon />
                        標記已處理
                      </Button>
                    ) : (
                      <Button
                        size="xs"
                        variant="ghost"
                        onClick={() => void handleToggleState(mention.id, 'pending')}
                      >
                        <UndoIcon />
                        退回待處理
                      </Button>
                    )}
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}
