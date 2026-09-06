import { useEffect, useMemo } from 'react'
import {
  AlertCircleIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ImageIcon,
  Loader2Icon,
  MessagesSquareIcon,
  RefreshCwIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import {
  PREVIEW_LIMITS,
  buildPreviewItems,
  usePreviewStore,
  type PreviewLimit,
} from '@/store/preview'
import type { ChatMessage, Space } from '@/lib/types'

interface SpaceMessagePreviewProps {
  space: Space | null
  /** 有摘要在畫面上時預設收起來，讓摘要當主角 */
  defaultCollapsed?: boolean
}

/** 每一串一個顏色，掃一眼就分得出哪一列是哪一串。 */
const THREAD_ACCENTS = [
  'border-l-sky-500/70',
  'border-l-emerald-500/70',
  'border-l-amber-500/70',
  'border-l-violet-500/70',
  'border-l-rose-500/70',
  'border-l-teal-500/70',
]

/**
 * Space 訊息預覽：點一個 Space 就看得到最近幾則在講什麼，不必先跑一次摘要。
 *
 * **討論串在外層只佔一列**（`buildPreviewItems`），點開才展開內容。
 * 一開始是外層照樣把整串每一則印出來、點開又再印一次，同樣的訊息出現兩遍。
 * 點開時會順便把被 limit 切掉的部分補齊——按了才打 API，不預先撈。
 */
export function SpaceMessagePreview({ space, defaultCollapsed = false }: SpaceMessagePreviewProps) {
  const {
    spaceId,
    limit,
    messages,
    loading,
    error,
    openThreads,
    expanded,
    expanding,
    collapsedOverride,
    load,
    setLimit,
    toggleThread,
    setCollapsed,
  } = usePreviewStore()

  const currentSpaceId = space?.id ?? null
  useEffect(() => {
    if (currentSpaceId) void load(currentSpaceId)
  }, [currentSpaceId, load])

  // 換 Space 的那一瞬間 store 裡還是舊的資料，不要拿別的 Space 的訊息充數
  const showing = spaceId && spaceId === currentSpaceId ? messages : []
  const items = useMemo(() => buildPreviewItems(showing), [showing])
  const threadCount = items.filter((i) => i.kind === 'thread').length
  // 使用者沒表示過意見就跟隨預設（有摘要時收起來）；按過就一直聽他的
  const isCollapsed = collapsedOverride ?? defaultCollapsed

  if (!space) return null

  return (
    <section className="rounded-xl border border-border bg-card/40">
      <header className="flex flex-wrap items-center gap-2 border-b border-border/70 px-3 py-2">
        <button
          type="button"
          className="flex items-center gap-1.5 text-xs font-medium"
          onClick={() => setCollapsed(!isCollapsed)}
          aria-expanded={!isCollapsed}
        >
          {isCollapsed ? (
            <ChevronRightIcon className="size-3.5" />
          ) : (
            <ChevronDownIcon className="size-3.5" />
          )}
          <MessagesSquareIcon className="size-3.5 text-sky-500" />
          最近訊息
        </button>

        {/* 則數選擇。刻意與摘要的「抓取則數」分開——那個是要摘多少，
            這個是我想先瞄幾則，共用會讓調預覽意外改到摘要範圍。 */}
        <div className="flex items-center gap-1" role="group" aria-label="預覽則數">
          {PREVIEW_LIMITS.map((n) => (
            <button
              key={n}
              type="button"
              onClick={() => setLimit(n as PreviewLimit)}
              className={cn(
                'rounded border px-1.5 py-0.5 text-[11px] transition-colors',
                limit === n
                  ? 'border-sky-500/50 bg-sky-500/15 font-medium text-sky-600 dark:text-sky-400'
                  : 'border-border text-muted-foreground hover:border-border/80 hover:bg-accent/50',
              )}
              aria-pressed={limit === n}
            >
              {n}
            </button>
          ))}
          <span className="ml-0.5 text-[11px] text-muted-foreground">則</span>
        </div>

        {!isCollapsed ? (
          <span className="text-[11px] text-muted-foreground">
            {loading ? '讀取中…' : `顯示 ${showing.length} 則`}
            {threadCount > 0 ? ` · ${threadCount} 個討論串（點開看）` : ''}
          </span>
        ) : null}

        <Button
          size="xs"
          variant="ghost"
          className="ml-auto"
          onClick={() => void load(space.id, { force: true })}
          disabled={loading}
          title="重新讀取（訊息是即時取回的，不進資料庫）"
        >
          {loading ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />}
          重新讀取
        </Button>
      </header>

      {isCollapsed ? null : (
        <div className="max-h-[46vh] overflow-y-auto p-2">
          {error ? (
            <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-2.5 text-[11px] text-destructive">
              <AlertCircleIcon className="mt-0.5 size-3 shrink-0" />
              <span>{error}</span>
            </div>
          ) : null}

          {!loading && !error && items.length === 0 ? (
            <p className="px-2 py-6 text-center text-xs text-muted-foreground">
              這個 Space 讀不到任何訊息。
            </p>
          ) : null}

          <ul className="space-y-1">
            {items.map((item) =>
              item.kind === 'message' ? (
                <li key={item.key}>
                  <MessageRow message={item.message} />
                </li>
              ) : (
                <li key={item.key}>
                  <ThreadRow
                    accentIndex={item.index - 1}
                    open={openThreads.includes(item.threadName)}
                    loading={expanding === item.threadName}
                    // 整串抓回來之前先用視窗裡已有的那幾則頂著，點下去立刻有反應
                    messages={expanded[item.threadName] ?? item.messages}
                    windowCount={item.messages.length}
                    onToggle={() => toggleThread(space.id, item.threadName)}
                  />
                </li>
              ),
            )}
          </ul>
        </div>
      )}
    </section>
  )
}

/** 收合起來的一整串。外層只佔這一列，內容要點開才出現。 */
function ThreadRow({
  accentIndex,
  open,
  loading,
  messages,
  windowCount,
  onToggle,
}: {
  accentIndex: number
  open: boolean
  loading: boolean
  messages: ChatMessage[]
  windowCount: number
  onToggle: () => void
}) {
  const accent = THREAD_ACCENTS[accentIndex % THREAD_ACCENTS.length]
  const last = messages[messages.length - 1]
  const senders = [...new Set(messages.map((m) => m.sender))]
  const preview = last?.text?.trim() || last?.attachment_note || ''

  return (
    <div className={cn('rounded-r border-l-2', accent)}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-start gap-1.5 rounded-r bg-background/40 py-1 pr-2 pl-1.5 text-left hover:bg-accent/40"
      >
        {open ? (
          <ChevronDownIcon className="mt-0.5 size-3 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRightIcon className="mt-0.5 size-3 shrink-0 text-muted-foreground" />
        )}
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span className="rounded bg-muted px-1 text-[10px] font-medium text-muted-foreground">
              討論串 {messages.length} 則
            </span>
            <span className="truncate text-[11px] font-medium">{senders.join('、')}</span>
            {last ? (
              <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                {last.time}
              </span>
            ) : null}
            {loading ? (
              <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                <Loader2Icon className="size-2.5 animate-spin" />
                補齊整串中…
              </span>
            ) : null}
          </span>
          {!open && preview ? (
            <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
              {preview}
            </span>
          ) : null}
        </span>
      </button>

      {open ? (
        <ul className="space-y-1 border-l border-dashed border-border/70 py-1 pl-2 ml-2">
          {messages.map((m) => (
            <li key={m.name}>
              <MessageRow message={m} />
            </li>
          ))}
          {/* 整串比視窗裡看得到的多，講清楚多出來的是從哪來的。
              「最近 N 則」是按時間取的，常常把一串切成片段——不說的話
              使用者會以為這幾則本來就在清單裡，只是他沒看到。 */}
          {messages.length > windowCount ? (
            <li className="pl-2 text-[10px] text-muted-foreground">
              其中 {messages.length - windowCount} 則原本不在上面的清單範圍內，
              是展開這一串時補回來的
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  )
}

function MessageRow({ message }: { message: ChatMessage }) {
  return (
    // data-message-name 是給 e2e 驗「同一則不會出現兩次」用的。
    // 收合前後都只該有一個——重複顯示正是這個面板最早的缺陷。
    <div data-message-name={message.name} className="rounded bg-background/40 px-2 py-1">
      <div className="flex items-baseline gap-2">
        <span className="truncate text-[11px] font-medium">{message.sender}</span>
        <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
          {message.time}
        </span>
      </div>
      {message.text ? (
        <p className="mt-0.5 text-[11px] leading-relaxed whitespace-pre-wrap">{message.text}</p>
      ) : null}
      {/* 只有圖、沒有文字的訊息以前在這個端點會整則消失。附件一定要看得見，
          否則使用者會覺得「我要 20 則怎麼只有 17 則」而找不到原因。 */}
      {message.attachment_note ? (
        <p className="mt-0.5 flex items-start gap-1 text-[10px] text-muted-foreground">
          <ImageIcon className="mt-0.5 size-3 shrink-0" />
          <span className="min-w-0 break-all">{message.attachment_note}</span>
        </p>
      ) : null}
    </div>
  )
}
