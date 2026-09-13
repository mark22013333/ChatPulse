interface PulseMarkProps {
  className?: string
}

/**
 * ChatPulse 的識別標記：一條脈搏線。
 *
 * 與 `index.html` 的 favicon 是同一條 path——在此之前 app 內用的是閃電
 * （ZapIcon），與產品名和 favicon 都對不上。尺寸由 `className` 的 `size-*`
 * 控制，用法與 lucide 的圖示一致。
 */
export function PulseMark({ className }: PulseMarkProps) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M4 12h3l2-6 4 12 2-6h5" />
    </svg>
  )
}
