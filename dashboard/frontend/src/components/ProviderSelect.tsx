import { BookmarkCheckIcon, Loader2Icon } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { AUTO_PROVIDER, useProviderStore } from '@/store/providers'

interface ProviderSelectProps {
  /** 給 Label 的 htmlFor 用，兩個工作區各自要不同的 id */
  id: string
  /** 串流中就不讓改 */
  disabled?: boolean
  /** 觸發器寬度（Draft 側欄比較窄） */
  triggerClassName?: string
  className?: string
}

/**
 * AI 供應商選擇器（摘要工作台與 Draft Reply 共用）。
 *
 * 兩個刻意的設計：
 * 1. 永遠有一個「自動（伺服器決定）」選項——伺服器的 `default` 可能是別名
 *    （`claude`／`auto`），對不上任何 provider name，沒有這個選項就沒得選。
 * 2. `available: false` 的供應商照樣列出來，但不可選，並把 `reason` 原文顯示在
 *    選項下方。那行寫的是「要設哪個環境變數」，吞掉的話使用者根本不知道怎麼修。
 */
export function ProviderSelect({ id, disabled, triggerClassName, className }: ProviderSelectProps) {
  const providers = useProviderStore((state) => state.providers)
  const serverDefault = useProviderStore((state) => state.serverDefault)
  const savedDefault = useProviderStore((state) => state.savedDefault)
  const selected = useProviderStore((state) => state.selected)
  const saving = useProviderStore((state) => state.saving)
  const setSelected = useProviderStore((state) => state.setSelected)
  const saveAsDefault = useProviderStore((state) => state.saveAsDefault)

  const autoLabel = serverDefault ? `自動（伺服器預設：${serverDefault}）` : '自動（伺服器決定）'

  // Base UI 的 Select 用 items 這份對照表決定觸發器上顯示的文字
  const items: Record<string, string> = {
    [AUTO_PROVIDER]: autoLabel,
    ...Object.fromEntries(providers.map((item) => [item.name, item.label])),
  }

  // 目前選擇還沒存成偏好時，才顯示「設為預設」
  const savedEquivalent = savedDefault ?? AUTO_PROVIDER
  const canSaveDefault = selected !== savedEquivalent

  const handleSave = async () => {
    const ok = await saveAsDefault()
    if (ok) {
      toast.success(
        selected === AUTO_PROVIDER
          ? '已清除供應商偏好，之後沿用伺服器預設'
          : `已把「${items[selected] ?? selected}」設為預設供應商`,
      )
    } else {
      toast.error(useProviderStore.getState().error ?? '儲存偏好失敗')
    }
  }

  return (
    <div className={cn('flex flex-col gap-1', className)}>
      <div className="flex items-center gap-1">
        <Label htmlFor={id} className="text-xs text-muted-foreground">
          AI 供應商
        </Label>
        {canSaveDefault ? (
          <Button
            type="button"
            size="xs"
            variant="ghost"
            className="h-4 px-1 text-2xs text-muted-foreground"
            disabled={saving}
            onClick={() => void handleSave()}
          >
            {saving ? (
              <Loader2Icon className="size-3 animate-spin" />
            ) : (
              <BookmarkCheckIcon className="size-3" />
            )}
            設為預設
          </Button>
        ) : null}
      </div>

      <Select items={items} value={selected} onValueChange={(value) => setSelected(value as string)}>
        <SelectTrigger id={id} size="sm" className={cn('w-44', triggerClassName)} disabled={disabled}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent className="w-auto max-w-96 min-w-72">
          <SelectItem value={AUTO_PROVIDER}>
            <span className="flex w-full flex-col gap-0.5 whitespace-normal">
              <span className="font-medium">{autoLabel}</span>
              <span className="text-2xs leading-snug text-muted-foreground">
                不指定供應商，由伺服器依你的偏好或自身預設決定。
              </span>
            </span>
          </SelectItem>

          {providers.map((item) => (
            <SelectItem
              key={item.name}
              value={item.name}
              disabled={!item.available}
              // reason 在下面已經是可見文字（不可用時還是 caution 色），
              // 放進 tooltip 是真重複——而且 disabled 的項目摸不到 tooltip
            >
              <span className="flex w-full flex-col gap-0.5 whitespace-normal">
                <span className="flex flex-wrap items-center gap-1.5">
                  <span className="font-medium">{item.label}</span>
                  {item.available ? null : (
                    <span className="rounded border border-caution-line bg-caution/10 px-1 py-px text-2xs text-caution">
                      無法使用
                    </span>
                  )}
                </span>
                <span className="metric text-2xs text-muted-foreground">{item.model}</span>
                {item.reason ? (
                  <span
                    className={cn(
                      'text-2xs leading-snug',
                      item.available ? 'text-muted-foreground' : 'text-caution',
                    )}
                  >
                    {item.reason}
                  </span>
                ) : null}
              </span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
