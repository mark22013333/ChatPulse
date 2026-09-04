import { ActivityIcon, AlertTriangleIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { formatDateTime, formatNumber, relativeTime } from '@/lib/format'
import { useAuthStore } from '@/store/auth'

/** 採集器狀態（GET /api/v1/me 的 collector 欄位）。 */
export function CollectorPanel() {
  const collector = useAuthStore((state) => state.me?.collector ?? null)

  return (
    <section className="shrink-0 border-b border-border px-3 py-2.5">
      <div className="mb-2 flex items-center gap-2">
        <ActivityIcon className="size-3.5 text-muted-foreground" />
        <h3 className="text-xs font-semibold">採集器狀態</h3>
        {collector ? (
          <span
            className={cn(
              'ml-auto flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px]',
              collector.running
                ? 'bg-emerald-500/10 text-emerald-500'
                : 'bg-muted text-muted-foreground',
            )}
          >
            <span
              className={cn(
                'size-1.5 rounded-full',
                collector.running ? 'animate-pulse bg-emerald-500' : 'bg-muted-foreground',
              )}
            />
            {collector.running ? '運行中' : '未運行'}
          </span>
        ) : null}
      </div>

      {!collector ? (
        <p className="text-[11px] text-muted-foreground">尚未取得採集器資訊。</p>
      ) : (
        <dl className="space-y-1 text-[11px]">
          <Row label="實作" value={collector.implementation} />
          <Row label="間隔" value={`${collector.interval_seconds} 秒`} />
          <Row
            label="上次輪詢"
            value={
              collector.last_polled_at
                ? `${relativeTime(collector.last_polled_at)}（${formatDateTime(collector.last_polled_at)}）`
                : '尚未輪詢'
            }
          />
          {collector.last_run_stats ? (
            <>
              <Row
                label="上輪新增"
                value={`${formatNumber(collector.last_run_stats.new_mentions ?? 0)} 則 Mention`}
              />
              <Row
                label="上輪掃描"
                value={`${formatNumber(collector.last_run_stats.spaces_polled ?? 0)} / ${formatNumber(
                  collector.last_run_stats.spaces_total ?? 0,
                )} 個 Space`}
              />
              <Row
                label="上輪耗時"
                value={`${collector.last_run_stats.elapsed_seconds ?? 0} 秒 · ${formatNumber(
                  collector.last_run_stats.api_calls ?? 0,
                )} 次 API`}
              />
            </>
          ) : null}
        </dl>
      )}

      {collector?.last_error ? (
        <div className="mt-2 flex items-start gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 p-2 text-[11px] text-amber-600 dark:text-amber-400">
          <AlertTriangleIcon className="mt-0.5 size-3 shrink-0" />
          <span className="leading-relaxed break-words">{collector.last_error}</span>
        </div>
      ) : null}
    </section>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className="truncate text-right">{value}</dd>
    </div>
  )
}
