import { useEffect, useRef } from 'react'
import { XIcon } from 'lucide-react'
import { EvidenceColumn } from '@/components/evidence/EvidenceColumn'
import { Button } from '@/components/ui/button'

interface EvidenceDrawerProps {
  open: boolean
  origin: 'summary' | 'draft'
  onClose: () => void
}

/**
 * 1280px 以下的證據抽屜（設計規格 §12）。
 *
 * **刻意不自動打開**——它會蓋住正在讀的草稿。由頂列的證據鈕或 ⌘J 開啟。
 *
 * 開著時**不 inert 主區**：使用者要能一邊看證據一邊改回話，這不是模態。
 * 因此也沒有背景遮罩——遮罩會暗示「先處理我」，而它只是參考資訊。
 */
export function EvidenceDrawer({ open, origin, onClose }: EvidenceDrawerProps) {
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  useEffect(() => {
    if (open) panelRef.current?.focus()
  }, [open])

  if (!open) return null

  return (
    <div
      ref={panelRef}
      tabIndex={-1}
      role="complementary"
      aria-label="證據"
      className="border-line bg-background shadow-overlay fixed top-12 right-0 bottom-0 z-40 flex w-rail flex-col border-l outline-none"
    >
      <div className="border-line flex shrink-0 items-center justify-end border-b px-gutter-tight py-1">
        <Button size="sm" variant="ghost" onClick={onClose}>
          <XIcon />
          關閉
        </Button>
      </div>
      <div className="min-h-0 flex-1">
        <EvidenceColumn origin={origin} />
      </div>
    </div>
  )
}
