import { useEffect, useState } from 'react'
import { AlertCircleIcon, ArrowLeftIcon, Loader2Icon, RefreshCwIcon } from 'lucide-react'
import { PulseMark } from '@/app/PulseMark'
import { CollectorPanel } from '@/components/CollectorPanel'
import { Button } from '@/components/ui/button'
import { api, errorMessage } from '@/lib/api'
import { useRouter } from '@/router/useRouter'
import type { HealthResponse } from '@/lib/types'

interface Row {
  label: string
  value: string
  hint?: string
  /** 這一項是不是「不太對」，需要視覺上被看見 */
  caution?: boolean
}

function toRows(health: HealthResponse): Row[] {
  return [
    { label: '服務', value: health.status === 'ok' ? '正常' : health.status },
    {
      label: '資料庫',
      value: health.db,
      hint: 'SQLite 的 journal mode，正常是 wal',
      caution: health.db !== 'wal',
    },
    {
      label: '採集器',
      value: health.collector_running ? '執行中' : '沒有在跑',
      hint: `實作：${health.collector_implementation}`,
      caution: !health.collector_running,
    },
    { label: '預設供應商', value: health.ai_provider_default },
    {
      label: '實際使用',
      value: health.ai_provider_active,
      hint: '別名解析後真正會用的那一個',
    },
    {
      label: 'Gemini 金鑰',
      value: health.gemini_configured ? '已設定' : '沒有設定',
      caution: !health.gemini_configured,
    },
    { label: '已授權的 Viewer', value: String(health.viewer_count) },
  ]
}

/**
 * 診斷頁（設計規格 §6.5）。
 *
 * **刻意排在登入 gate 之前**：「後端起來了嗎、AI 供應商設好了嗎」正是還沒
 * 登入時最需要問的事，擋在登入後面等於在最需要它的時候看不到。
 *
 * P1 先做到把 `/health` 攤開來看。P3 會再補上 Sepia 規則版本、採集器上次
 * 輪詢時間，以及目前散落在各處的實作細節文案（登入畫面的環境變數名等）。
 */
export function DiagnosticsPage() {
  const { navigate } = useRouter()
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      setHealth(await api.health())
    } catch (err) {
      setError(errorMessage(err))
      setHealth(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  return (
    <div className="flex min-h-dvh flex-col bg-background text-foreground">
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line px-gutter">
        <span className="flex size-6 items-center justify-center rounded-md bg-signal-wash text-signal ring-1 ring-signal-line">
          <PulseMark className="size-3.5" />
        </span>
        <span className="text-sm font-semibold tracking-tight">ChatPulse</span>
        <span className="text-fg-dim text-xs">診斷</span>
        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={() => void load()} disabled={loading}>
            {loading ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />}
            重新檢查
          </Button>
          <Button size="sm" variant="ghost" onClick={() => navigate('#/summary')}>
            <ArrowLeftIcon />
            回到工作台
          </Button>
        </div>
      </header>

      <main className="mx-auto w-full max-w-2xl px-gutter py-8">
        <h1 className="text-md font-semibold">服務狀態</h1>
        <p className="text-fg-dim mt-1 text-xs">
          這一頁不需要登入就看得到。裝好之後第一件要確認的事，就在這裡。
        </p>

        {error ? (
          <div className="border-destructive/30 bg-destructive/10 text-destructive mt-4 flex items-start gap-2 rounded-lg border p-3 text-xs">
            <AlertCircleIcon className="mt-0.5 size-3.5 shrink-0" />
            <span className="leading-relaxed">
              {error}
              <br />
              後端可能沒有啟動。確認一下跑 <code className="metric">./chatpulse.sh web</code>{' '}
              的那個終端還在不在。
            </span>
          </div>
        ) : null}

        {health ? (
          <dl className="mt-5">
            {toRows(health).map((row) => (
              <div
                key={row.label}
                className="border-line-evidence grid grid-cols-[7rem_1fr] gap-x-4 border-b py-2.5 last:border-b-0"
              >
                <dt className="text-fg-dim text-xs">{row.label}</dt>
                <dd className="min-w-0">
                  <span className={row.caution ? 'text-caution text-sm' : 'text-sm'}>
                    {row.value}
                    {row.caution ? ' †' : null}
                  </span>
                  {row.hint ? <p className="text-fg-subtle text-2xs mt-0.5">{row.hint}</p> : null}
                </dd>
              </div>
            ))}
          </dl>
        ) : null}

        {loading && !health ? (
          <p className="text-fg-dim mt-5 flex items-center gap-2 text-xs">
            <Loader2Icon className="size-3.5 animate-spin" />
            正在向後端確認…
          </p>
        ) : null}

        {/* 採集器狀態從草稿工作區的右欄搬到這裡：它是運維資訊，不是證據。
            右欄現在只回答「這份產出建立在什麼之上」。 */}
        <section className="mt-8">
          <h2 className="text-md font-semibold">採集器</h2>
          <p className="text-fg-dim mt-1 text-xs">
            Mention 是後端輪詢 Google Chat 取得的（ADR-0004）。這裡看得到它有沒有在跑、
            上一輪掃了多少。
          </p>
          <div className="mt-3">
            <CollectorPanel />
          </div>
        </section>
      </main>
    </div>
  )
}
