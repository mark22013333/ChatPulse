import type { ReactNode } from 'react'
import { Loader2Icon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: ReactNode
  /** 送出前要讓 Viewer 讀過的內容（訊息全文預覽） */
  preview?: string
  previewLabel?: string
  confirmLabel?: string
  cancelLabel?: string
  pending?: boolean
  destructive?: boolean
  onConfirm: () => void
}

/**
 * 任何送出動作前的二次確認（規格 5.4、7.2 步驟 6）。
 * 對話框一定要顯示目標與訊息全文，讓 Viewer 在按下確認之前看得到會送出什麼。
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  preview,
  previewLabel = '訊息全文預覽',
  confirmLabel = '確認送出',
  cancelLabel = '取消',
  pending = false,
  destructive = false,
  onConfirm,
}: ConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </DialogHeader>

        {preview !== undefined ? (
          <div className="space-y-1.5">
            <p className="text-xs font-medium text-muted-foreground">{previewLabel}</p>
            <pre className="max-h-72 overflow-auto rounded-lg border border-border bg-muted/40 p-3 text-xs leading-relaxed whitespace-pre-wrap break-words">
              {preview || '（空白內容）'}
            </pre>
          </div>
        ) : null}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>
            {cancelLabel}
          </Button>
          <Button
            variant={destructive ? 'destructive' : 'default'}
            onClick={onConfirm}
            disabled={pending}
          >
            {pending ? <Loader2Icon className="animate-spin" /> : null}
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
