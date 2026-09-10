import { useEffect, useMemo } from 'react'
import {
  AlertCircleIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  Loader2Icon,
  MessagesSquareIcon,
  RefreshCwIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { MessageRow } from '@/components/preview/MessageRow'
import { ThreadRow } from '@/components/preview/ThreadRow'
import { cn } from '@/lib/utils'
import {
  PREVIEW_LIMITS,
  buildPreviewItems,
  usePreviewStore,
  type PreviewLimit,
} from '@/store/preview'
import type { Space } from '@/lib/types'

interface SpaceMessagePreviewProps {
  space: Space | null
  /** 有摘要在畫面上時預設收起來，讓摘要當主角 */
  defaultCollapsed?: boolean
}

/**
 * Space 訊息預覽：點一個 Space 就看得到最近幾則在講什麼，不必先跑一次摘要。
 *
 * **討論串在外層只佔一列**（`buildPreviewItems`），點開才展開內容。
 * 一開始是外層照樣把整串每一則印出來、點開又再印一次，同樣的訊息出現兩遍。
 * 點開時會順便把被 limit 切掉的部分補齊——按了才打 API，不預先撈。
 *
 * 兩種列各自成檔（`preview/MessageRow`、`preview/ThreadRow`），這裡只留外殼：
 * 收合、則數、重新讀取，以及「哪些列是訊息、哪些列是討論串」的組裝。
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
          <MessagesSquareIcon className="size-3.5 text-signal" />
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
                'rounded border px-1.5 py-0.5 text-xs transition-colors',
                limit === n
                  ? 'border-signal-line bg-signal-wash font-medium text-signal'
                  : 'border-border text-muted-foreground hover:border-border/80 hover:bg-accent/50',
              )}
              aria-pressed={limit === n}
            >
              {n}
            </button>
          ))}
          <span className="ml-0.5 text-xs text-muted-foreground">則</span>
        </div>

        {!isCollapsed ? (
          <span className="text-xs text-muted-foreground">
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
        >
          {loading ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />}
          重新讀取
        </Button>
      </header>

      {isCollapsed ? null : (
        <div className="max-h-[46vh] overflow-y-auto p-2">
          {error ? (
            <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-2.5 text-xs text-destructive">
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

          {/* 「訊息是即時取回的，不進資料庫」原本只活在「重新讀取」那顆按鈕
              的 tooltip 裡。它回答的是「我看到的這些有多新、會不會被存起來」
              ——那是決策資訊，不該藏起來（規格 §10.3）。 */}
          <p className="text-fg-subtle px-1 pt-2 text-2xs">
            訊息是即時向 Google 取回的，不會存進資料庫；按「重新讀取」拿最新的。
          </p>
        </div>
      )}
    </section>
  )
}
