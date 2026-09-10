import { useState } from 'react'
import { HistoryIcon, Loader2Icon } from 'lucide-react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Markdown } from '@/components/Markdown'
import { formatDateTime } from '@/lib/format'
import { useSummaryStore } from '@/store/summary'
import type { SummaryRecord } from '@/lib/types'

const STYLE_LABEL: Record<string, string> = {
  general: '通用',
  technical: '技術細節',
  action_only: '只要待辦',
}

/** 歷史 Summary 清單（GET /api/v1/summaries，只會有自己產生的）。 */
export function SummaryHistory() {
  const history = useSummaryStore((state) => state.history)
  const loading = useSummaryStore((state) => state.historyLoading)
  const [opened, setOpened] = useState<SummaryRecord | null>(null)

  return (
    <section className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-2 px-3 py-2">
        <HistoryIcon className="size-3.5 text-muted-foreground" />
        <h3 className="text-xs font-semibold">歷史 Summary</h3>
        {loading ? <Loader2Icon className="size-3 animate-spin text-muted-foreground" /> : null}
        <span className="ml-auto text-xs text-muted-foreground">{history.length} 份</span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {history.length === 0 && !loading ? (
          <p className="px-1 py-3 text-xs leading-relaxed text-muted-foreground">
            還沒有產生過 Summary。產生後會自動出現在這裡（僅你本人可見）。
          </p>
        ) : null}

        <ul className="space-y-1">
          {history.map((record) => (
            <li key={record.id}>
              <button
                type="button"
                onClick={() => setOpened(record)}
                className="w-full rounded-lg border border-transparent px-2 py-1.5 text-left transition-colors hover:border-border hover:bg-accent/50"
              >
                <span className="block truncate text-xs font-medium">{record.space_name}</span>
                <span className="mt-0.5 block truncate text-2xs text-muted-foreground">
                  {formatDateTime(record.created_at)} · {STYLE_LABEL[record.style] ?? record.style} ·{' '}
                  {record.message_count} 則
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <Dialog open={opened !== null} onOpenChange={(open) => !open && setOpened(null)}>
        <DialogContent className="sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>{opened?.space_name ?? ''}</DialogTitle>
            <DialogDescription>
              {opened
                ? `${formatDateTime(opened.created_at)} · ${STYLE_LABEL[opened.style] ?? opened.style} · ${opened.message_count} 則`
                : ''}
            </DialogDescription>
          </DialogHeader>
          <Markdown
            source={opened?.content_md ?? ''}
            className="max-h-[60vh] overflow-y-auto rounded-lg border border-border bg-muted/30 p-4"
          />
        </DialogContent>
      </Dialog>
    </section>
  )
}
