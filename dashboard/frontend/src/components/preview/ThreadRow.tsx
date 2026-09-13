import { ChevronDownIcon, ChevronRightIcon, Loader2Icon } from 'lucide-react'
import { MessageRow } from '@/components/preview/MessageRow'
import { cn } from '@/lib/utils'
import type { ChatMessage } from '@/lib/types'

/**
 * 每一串一個色階，掃一眼就分得出哪一列是哪一串。
 * 分群不是狀態，所以走單色系的明度階梯而不是換色相。
 */
const THREAD_ACCENTS = [
  'border-l-signal',
  'border-l-signal/60',
  'border-l-signal/35',
  'border-l-line-strong',
]

interface ThreadRowProps {
  accentIndex: number
  open: boolean
  loading: boolean
  messages: ChatMessage[]
  /** 視窗（最近 N 則）裡本來看得到幾則。用來算「補回來的有幾則」。 */
  windowCount: number
  onToggle: () => void
}

/** 收合起來的一整串。外層只佔這一列，內容要點開才出現。 */
export function ThreadRow({
  accentIndex,
  open,
  loading,
  messages,
  windowCount,
  onToggle,
}: ThreadRowProps) {
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
            <span className="rounded bg-muted px-1 text-2xs font-medium text-muted-foreground">
              討論串 {messages.length} 則
            </span>
            <span className="truncate text-xs font-medium">{senders.join('、')}</span>
            {last ? (
              <span className="metric shrink-0 text-2xs text-muted-foreground">{last.time}</span>
            ) : null}
            {loading ? (
              <span className="flex items-center gap-1 text-2xs text-muted-foreground">
                <Loader2Icon className="size-2.5 animate-spin" />
                補齊整串中…
              </span>
            ) : null}
          </span>
          {!open && preview ? (
            <span className="mt-0.5 block truncate text-xs text-muted-foreground">{preview}</span>
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
            <li className="pl-2 text-2xs text-muted-foreground">
              其中 {messages.length - windowCount} 則原本不在上面的清單範圍內，
              是展開這一串時補回來的
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  )
}
