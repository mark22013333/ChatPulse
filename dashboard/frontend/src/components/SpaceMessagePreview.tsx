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
import { PREVIEW_LIMITS, threadGroups, usePreviewStore, type PreviewLimit } from '@/store/preview'
import type { ChatMessage, Space } from '@/lib/types'

interface SpaceMessagePreviewProps {
  space: Space | null
  /** 有摘要在畫面上時預設收起來，讓摘要當主角 */
  defaultCollapsed?: boolean
}

/** 同一串的訊息用同一個顏色，掃一眼就分得出哪幾則是同一段對話。 */
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
 * **討論串回覆本來就在裡面**——Google 的 `messages.list` 回的是扁平訊息流，
 * 沒有參數可以排除它們。問題是它們混在時間序裡看不出結構，而且「最近 N 則」
 * 常常把一串切成片段。所以這裡做兩件事：
 *   1. 同一串的訊息標上同色左邊框與「討論串 N」徽章（只標視窗內不只一則的，
 *      否則私訊每則各自一串，全部都有徽章等於沒標）
 *   2. 那些串可以按「展開整串」補齊被切掉的部分（按了才打 API，不預先撈）
 */
export function SpaceMessagePreview({ space, defaultCollapsed = false }: SpaceMessagePreviewProps) {
  const {
    spaceId,
    limit,
    messages,
    loading,
    error,
    expanded,
    expanding,
    collapsedOverride,
    load,
    setLimit,
    expandThread,
    collapseThread,
    setCollapsed,
  } = usePreviewStore()

  const currentSpaceId = space?.id ?? null
  useEffect(() => {
    if (currentSpaceId) void load(currentSpaceId)
  }, [currentSpaceId, load])

  const groups = useMemo(() => threadGroups(messages), [messages])
  // 使用者沒表示過意見就跟隨預設（有摘要時收起來）；按過就一直聽他的
  const isCollapsed = collapsedOverride ?? defaultCollapsed

  if (!space) return null

  // 換 Space 的那一瞬間 store 裡還是舊的資料，不要拿別的 Space 的訊息充數
  const showing = spaceId === space.id ? messages : []

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
            {groups.size > 0 ? ` · ${groups.size} 個討論串` : ''}
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

          {!loading && !error && showing.length === 0 ? (
            <p className="px-2 py-6 text-center text-xs text-muted-foreground">
              這個 Space 讀不到任何訊息。
            </p>
          ) : null}

          <ul className="space-y-1">
            {showing.map((m) => {
              const group = m.thread_name ? groups.get(m.thread_name) : undefined
              const full = m.thread_name ? expanded[m.thread_name] : undefined
              // 展開後只在該串的第一則底下印整串，不要每一則都印一次
              const isFirstOfThread =
                group && showing.find((x) => x.thread_name === m.thread_name)?.name === m.name
              return (
                <li key={m.name}>
                  <MessageRow message={m} accentIndex={group ? group.index - 1 : undefined} />
                  {group && isFirstOfThread ? (
                    <div className="mt-0.5 mb-1 pl-4">
                      <button
                        type="button"
                        className="text-[10px] text-sky-600 hover:underline dark:text-sky-400"
                        onClick={() =>
                          full
                            ? collapseThread(m.thread_name!)
                            : void expandThread(space.id, m.thread_name!)
                        }
                      >
                        {expanding === m.thread_name
                          ? '讀取整串中…'
                          : full
                            ? `收起整串（共 ${full.length} 則）`
                            : `展開整串（這裡只看得到 ${group.countInWindow} 則）`}
                      </button>
                      {full ? (
                        <ul className="mt-1 space-y-1 border-l border-dashed border-border pl-2">
                          {full.map((t) => (
                            <li key={`full-${t.name}`}>
                              <MessageRow message={t} accentIndex={group.index - 1} muted />
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  ) : null}
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </section>
  )
}

function MessageRow({
  message,
  accentIndex,
  muted = false,
}: {
  message: ChatMessage
  accentIndex?: number
  muted?: boolean
}) {
  const accent =
    accentIndex === undefined
      ? 'border-l-transparent'
      : THREAD_ACCENTS[accentIndex % THREAD_ACCENTS.length]
  return (
    <div
      className={cn(
        'rounded-r border-l-2 py-1 pr-2 pl-2',
        accent,
        muted ? 'bg-transparent' : 'bg-background/40',
      )}
    >
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
