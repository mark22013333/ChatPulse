import { useEffect } from 'react'
import { GaugeIcon, Loader2Icon } from 'lucide-react'
import { formatNumber } from '@/lib/format'
import { totalTokens, useUsageStore } from '@/store/usage'

/** 每日 Token 用量（GET /api/v1/usage）。天數住在 store，設定頁改得動。 */
export function UsagePanel() {
  const rows = useUsageStore((s) => s.rows)
  const days = useUsageStore((s) => s.days)
  const loading = useUsageStore((s) => s.loading)
  const ensureLoaded = useUsageStore((s) => s.ensureLoaded)

  useEffect(() => {
    void ensureLoaded()
  }, [ensureLoaded])

  const total = totalTokens(rows)

  return (
    <section className="shrink-0 border-t border-border px-3 py-2.5">
      <div className="mb-2 flex items-center gap-2">
        <GaugeIcon className="size-3.5 text-muted-foreground" />
        <h3 className="text-xs font-semibold">Token 用量（近 {days} 天）</h3>
        {loading ? <Loader2Icon className="size-3 animate-spin text-muted-foreground" /> : null}
        <span className="ml-auto font-mono text-[10px] text-muted-foreground">
          {formatNumber(total)}
        </span>
      </div>

      {!loading && rows.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">尚無用量紀錄。</p>
      ) : null}

      {rows.length > 0 ? (
        <div className="max-h-44 overflow-y-auto">
          <table className="w-full text-[10px]">
            <thead className="sticky top-0 bg-background text-muted-foreground">
              <tr className="text-left">
                <th className="py-1 font-medium">日期</th>
                <th className="py-1 text-right font-medium">輸入</th>
                <th className="py-1 text-right font-medium">輸出</th>
                <th className="py-1 text-right font-medium">次數</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {rows.map((row) => (
                <tr key={`${row.day}-${row.model}`} className="border-t border-border/50">
                  <td className="py-1">{row.day}</td>
                  <td className="py-1 text-right">{formatNumber(row.prompt_tokens)}</td>
                  <td className="py-1 text-right">{formatNumber(row.output_tokens)}</td>
                  <td className="py-1 text-right">{formatNumber(row.calls)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  )
}
