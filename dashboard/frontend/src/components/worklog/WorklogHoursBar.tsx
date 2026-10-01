import { useEffect, useId, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { formatHours } from '@/store/worklog'
import { parseTotalHours } from '@/store/worklogDay'
import type { WorklogDay } from '@/lib/types'

/**
 * 已有草稿時的時數工具列（AC-4）：輸入當天總時數、重新分配。
 *
 * ### ⚠️ 「重新分配」其實是整次重新產生
 *
 * 後端沒有「只重算時數」的端點，所以這顆鈕走的是 draft/stream：重掃 git 與
 * Chat、重打 ZPlanner、重新分群。份量比名字重得多：改過的時數、備註會換成新產生
 * 的值；已選好的單**同一段還在就保留**，對不到新分段的照規格留空、不擋，重產完成後
 * 由容器在上方列出清掉了哪幾張單（done 事件的 `attributions_cleared`）。正因為不再有
 * 確認框，文案必須把這件事寫在鈕旁邊：Viewer 若以為只是「重算數字」，不會想到要回頭
 * 檢查哪幾段被清空了。
 *
 * 輸入框預填目前的 `total_hours`。它與各筆時數之和**不保證相等**（湊 0.5 倍數
 * 必然有餘數），真正會送出的數字在送出區。
 */
export function WorklogHoursBar({
  day,
  disabled = false,
  onRegenerate,
}: {
  day: WorklogDay
  disabled?: boolean
  onRegenerate: (totalHours: number | null) => void
}) {
  const initial = day.total_hours === null ? '' : formatHours(day.total_hours)
  const [hours, setHours] = useState(initial === '—' ? '' : initial)
  const [invalid, setInvalid] = useState(false)
  const inputId = useId()

  // 草稿被重新產生（total_hours 換了）時跟著換；Viewer 正在打的字不會被這個
  // 蓋掉，因為只有 total_hours 真的變了才會觸發
  useEffect(() => {
    const next = day.total_hours === null ? '' : formatHours(day.total_hours)
    setHours(next === '—' ? '' : next)
  }, [day.total_hours])

  const submit = () => {
    const parsed = parseTotalHours(hours)
    if (parsed === undefined) {
      setInvalid(true)
      return
    }
    setInvalid(false)
    onRegenerate(parsed)
  }

  return (
    <section aria-label="時數分配" className="border-line border-b pb-3">
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          submit()
        }}
      >
        <div className="flex flex-col gap-1">
          <label htmlFor={inputId} className="text-fg-dim text-xs">
            當天總時數
          </label>
          <Input
            id={inputId}
            aria-label="當天總時數"
            inputMode="decimal"
            placeholder="留白＝不分配"
            className="w-28"
            value={hours}
            aria-invalid={invalid || undefined}
            onChange={(event) => {
              setHours(event.target.value)
              if (invalid) setInvalid(false)
            }}
            disabled={disabled}
          />
        </div>
        <Button type="submit" variant="outline" disabled={disabled}>
          重新分配
        </Button>
      </form>
      {invalid ? (
        <p role="alert" className="text-destructive mt-2 text-xs">
          總時數要是大於 0 的數字（例如 8 或 7.5），或整個留白不分配。
        </p>
      ) : null}
      <p className="text-fg-subtle mt-2 max-w-prose text-xs leading-relaxed">
        重新分配＝<strong className="font-medium">重新產生整份草稿</strong>：會重掃 git 與 Chat、重新分群與分配時數，
        改過的時數與備註會換成新產生的值；已選好的單，同一段還在就會保留，對不到新分段的會清空，產生完會列出是哪幾張單。已送進 ZPlanner 的草稿不能重新產生。
      </p>
    </section>
  )
}
