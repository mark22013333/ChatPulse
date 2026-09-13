import { cn } from '@/lib/utils'
import type { EvidenceDetail, EvidenceItem, EvidenceStatus } from '@/lib/evidence'

/**
 * 規線＝語意（設計規格 §5.3）。
 *
 * solid／dashed／dotted 在 1px、灰階、色盲條件下都分得出來，而且「不連續的
 * 脈絡畫成一條斷掉的線」是語意直譯，不是為了無障礙才補的補丁。
 *
 * 承載語意的規線用 `--line-evidence`（3.49:1，受 WCAG 1.4.11 管），與純裝飾
 * 的 `--line`（1.27:1）是**不同的 token**。
 */
const RULE: Record<EvidenceStatus, string> = {
  ok: 'border-line-evidence border-solid',
  degraded: 'border-caution-line border-dashed',
  missing: 'border-line-evidence border-dotted',
  pending: 'border-line-evidence border-solid',
}

function DetailLine({ detail }: { detail: EvidenceDetail }) {
  if (detail.values?.length) {
    return (
      <div className="mt-0.5">
        {detail.label ? <span className="text-fg-dim text-2xs">{detail.label}</span> : null}
        <ul className="mt-0.5 space-y-0.5">
          {detail.values.map((value) => (
            <li key={value} className={cn('text-fg-dim text-2xs', detail.metric && 'metric')}>
              {value}
            </li>
          ))}
        </ul>
      </div>
    )
  }
  return (
    <p className="text-fg-dim text-2xs mt-0.5 flex justify-between gap-2">
      {detail.label ? <span>{detail.label}</span> : null}
      <span className={cn('min-w-0 truncate', detail.metric && 'metric', !detail.label && 'flex-1')}>
        {detail.value}
      </span>
    </p>
  )
}

function Row({ item }: { item: EvidenceItem }) {
  const hasDetail = item.detail.length > 0
  // critical 的列不准收合——誤判「這一送會結掉幾則」或「Sepia 到底有沒有生效」
  // 的代價太高，不該藏在一個要點開的三角形後面
  const alwaysOpen = item.critical === true

  const body = (
    <>
      {item.reason ? (
        <p
          className={cn(
            'text-2xs mt-1 leading-relaxed',
            item.status === 'degraded' ? 'text-caution' : 'text-fg-dim',
          )}
        >
          {item.status === 'degraded' ? '† ' : null}
          {item.reason}
        </p>
      ) : null}
      {item.detail.map((detail, index) => (
        <DetailLine key={`${item.id}-d${index}`} detail={detail} />
      ))}
      {item.action ? (
        <a href={item.action.href} className="text-signal text-2xs mt-1 inline-block underline">
          {item.action.label}
        </a>
      ) : null}
    </>
  )

  return (
    <div className={cn('border-b py-2 last:border-b-0', RULE[item.status])}>
      {/* 三欄：標籤 / 數字（固定寬、右對齊）/ 單位。
          所有列的數字因此共用同一個右緣——這是整套設計最主要的識別特徵。 */}
      <div className="grid grid-cols-[1fr_var(--container-metric)_1.75rem] items-baseline gap-x-2">
        <dt className="text-fg-dim min-w-0 truncate text-xs">{item.label}</dt>
        <dd className="col-span-2 grid grid-cols-subgrid items-baseline">
          {item.metric ? (
            <>
              <span className="metric text-foreground text-right text-sm">{item.metric.value}</span>
              <span className="text-fg-subtle text-2xs">{item.metric.unit}</span>
            </>
          ) : item.status === 'pending' ? (
            <span className="bg-signal col-span-2 inline-block h-3 w-0.5 animate-pulse" aria-hidden />
          ) : item.status === 'missing' ? (
            <span className="text-fg-subtle col-span-2 text-right text-sm">—</span>
          ) : (
            <span className="col-span-2" />
          )}
        </dd>
      </div>

      {item.summary ? (
        <p
          className={cn(
            'mt-0.5 text-xs',
            item.status === 'ok' && item.kind === 'polish' ? 'text-verified' : 'text-foreground',
          )}
        >
          {item.status === 'ok' && item.kind === 'polish' ? '✓ ' : null}
          {item.summary}
        </p>
      ) : null}

      {hasDetail || item.reason || item.action ? (
        alwaysOpen ? (
          <div>{body}</div>
        ) : (
          <details className="group">
            <summary className="text-fg-subtle text-2xs mt-0.5 cursor-pointer list-none">
              <span className="group-open:hidden">▸ 明細</span>
              <span className="hidden group-open:inline">▾ 明細</span>
            </summary>
            {body}
          </details>
        )
      ) : null}
    </div>
  )
}

export function EvidenceList({ items }: { items: EvidenceItem[] }) {
  return (
    <dl className="px-gutter-tight">
      {items.map((item) => (
        <Row key={item.id} item={item} />
      ))}
    </dl>
  )
}
