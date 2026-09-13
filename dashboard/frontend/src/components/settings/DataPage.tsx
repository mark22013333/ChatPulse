import { Loader2Icon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { formatNumber } from '@/lib/format'
import { cn } from '@/lib/utils'
import { totalTokens, USAGE_DAY_CHOICES, useUsageStore } from '@/store/usage'
import { useSummaryStore } from '@/store/summary'

/**
 * 資料頁：Token 用量與歷史 Summary 的範圍控制。
 *
 * 後端一直支援 `/usage?days=1~90` 與 `/summaries?limit=1~1000`，但前端把它們
 * 寫死成 14 天與 50 筆。這一頁把那兩個旋鈕交還給 Viewer。
 */
export function DataPage() {
  const rows = useUsageStore((s) => s.rows)
  const days = useUsageStore((s) => s.days)
  const loading = useUsageStore((s) => s.loading)
  const setDays = useUsageStore((s) => s.setDays)

  const history = useSummaryStore((s) => s.history)
  const historyLoading = useSummaryStore((s) => s.historyLoading)

  const total = totalTokens(rows)

  return (
    <div className="space-y-4">
      <section className="overflow-hidden rounded-xl border border-border bg-surface">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2">
          <h2 className="text-sm font-semibold">Token 用量</h2>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-fg-dim text-xs">統計範圍</span>
            {USAGE_DAY_CHOICES.map((choice) => (
              <Button
                key={choice}
                size="sm"
                variant={days === choice ? 'default' : 'outline'}
                onClick={() => void setDays(choice)}
                disabled={loading}
                aria-pressed={days === choice}
              >
                {choice} 天
              </Button>
            ))}
            {loading ? <Loader2Icon className="text-fg-dim size-4 animate-spin" /> : null}
          </div>
        </div>
        <div className="space-y-3 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-fg-dim text-xs">
              以每天、每個模型分開計。這裡選的天數會同步套用到右欄的用量面板。
            </p>
            <span className="metric text-fg-dim text-xs">
              合計 {formatNumber(total)}
            </span>
          </div>

          {rows.length === 0 && !loading ? (
            <p className="text-fg-dim text-xs">這段期間沒有用量紀錄。</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">近 {days} 天的每日 Token 用量</caption>
                <thead className="text-fg-dim border-line-evidence border-b">
                  <tr>
                    <th scope="col" className="py-2 text-left text-xs font-medium">
                      日期
                    </th>
                    <th scope="col" className="py-2 text-left text-xs font-medium">
                      模型
                    </th>
                    <th scope="col" className="py-2 text-right text-xs font-medium">
                      輸入
                    </th>
                    <th scope="col" className="py-2 text-right text-xs font-medium">
                      輸出
                    </th>
                    <th scope="col" className="py-2 text-right text-xs font-medium">
                      次數
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={`${row.day}-${row.model}`} className="border-line border-b last:border-0">
                      <td className="metric py-1.5">{row.day}</td>
                      <td className="metric text-fg-dim py-1.5 text-xs">{row.model}</td>
                      <td className="metric py-1.5 text-right">{formatNumber(row.prompt_tokens)}</td>
                      <td className="metric py-1.5 text-right">{formatNumber(row.output_tokens)}</td>
                      <td className="metric py-1.5 text-right">{formatNumber(row.calls)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      <section className="overflow-hidden rounded-xl border border-border bg-surface">
        <div className="border-b border-line px-4 py-2">
          <h2 className="text-sm font-semibold">歷史 Summary</h2>
        </div>
        <div className="space-y-2 p-4">
          <p className="text-fg-dim text-xs">
            只有你看得到。摘要與草稿明文保存 90 天。
          </p>
          <p className={cn('text-sm', history.length === 0 && 'text-fg-dim')}>
            {historyLoading
              ? '正在載入…'
              : history.length === 0
                ? '還沒有產生過 Summary。'
                : `目前有 ${history.length} 份。`}
          </p>
        </div>
      </section>
    </div>
  )
}
