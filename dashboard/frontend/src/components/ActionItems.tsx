import { useEffect, useMemo, useState } from 'react'
import { ClipboardCheckIcon, ListChecksIcon } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { actionItemsToMarkdown, extractActionItems } from '@/lib/actionItems'
import { copyText } from '@/lib/clipboard'

interface ActionItemsProps {
  markdown: string
}

/**
 * 從摘要 Markdown 萃取 `• [負責人] 任務` 轉為可勾選清單（規格 5.3）。
 * 勾選狀態只存在前端，供人邊看邊點；「複製為 Markdown」把整份清單帶走。
 */
export function ActionItems({ markdown }: ActionItemsProps) {
  const items = useMemo(() => extractActionItems(markdown), [markdown])
  const [checked, setChecked] = useState<Set<string>>(new Set())
  // 清單組成改變（換一份摘要）時清空勾選；串流過程中逐字增長不會誤清既有勾選
  const signature = useMemo(() => items.map((item) => item.key).join('|'), [items])

  useEffect(() => {
    setChecked((prev) => {
      const valid = new Set(items.map((item) => item.key))
      const next = new Set([...prev].filter((key) => valid.has(key)))
      return next.size === prev.size ? prev : next
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature])

  const toggle = (key: string) => {
    setChecked((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const handleCopy = async () => {
    if (items.length === 0) {
      toast.warning('目前沒有可複製的待辦')
      return
    }
    const ok = await copyText(actionItemsToMarkdown(items, checked))
    if (ok) toast.success(`已複製 ${items.length} 項待辦為 Markdown`)
    else toast.error('複製失敗，請手動選取內容')
  }

  return (
    // 與摘要卡片同一套語法：標頭帶（同色 ＋ 一條細線）＋ 內容區。
    // 兩張卡片長得一樣，是為了讓「這一頁有幾個區塊」用掃的就數得出來。
    <section className="overflow-hidden rounded-xl border border-border bg-surface">
      <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <ListChecksIcon className="size-4 text-verified" />
          Action Items
          <span className="text-xs font-normal text-muted-foreground">
            （{checked.size}/{items.length} 已勾選）
          </span>
        </h3>
        <Button size="sm" variant="outline" onClick={() => void handleCopy()}>
          <ClipboardCheckIcon />
          複製為 Markdown
        </Button>
      </div>

      <div className="p-4">
      {items.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          這份摘要沒有偵測到指派格式的待辦（`• [負責人] 任務`）。
        </p>
      ) : (
        <ul className="space-y-1.5">
          {items.map((item) => (
            <li key={item.key}>
              {/*
                項目底用 bg-background 而不是 bg-muted：muted 在淺色比 surface 暗、
                在深色比 surface 亮，拿它當「凹進去的欄位」兩個主題的方向會相反。
                background 在兩個主題都比 surface 暗一階，凹陷方向才一致。
              */}
              <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-line bg-background p-2 transition-colors hover:border-line-strong">
                <Checkbox
                  checked={checked.has(item.key)}
                  onCheckedChange={() => toggle(item.key)}
                  className="mt-0.5 shrink-0"
                />
                <span className="min-w-0 text-xs leading-relaxed">
                  {item.owner ? (
                    <span className="mr-1.5 rounded bg-signal-wash px-1.5 py-0.5 font-medium text-verified">
                      {item.owner}
                    </span>
                  ) : null}
                  <span className={checked.has(item.key) ? 'text-muted-foreground line-through' : ''}>
                    {item.task}
                  </span>
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}
      </div>
    </section>
  )
}
