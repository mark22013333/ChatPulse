import { useId, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { parseTotalHours } from '@/store/worklogDay'

/**
 * 選了日期、這一天還沒有草稿時的中欄（AC-1）。
 *
 * 總時數**可以先不填**：省略就不分配，草稿照樣有證據、分群與候選——那才是
 * 這個功能真正在解決的事（回想那天做了什麼）。填了才會出現時數。
 *
 * ⚠️ 看不懂的輸入（`8小時`、`-1`）不送出、就地講清楚。刻意不把它當成「空白」
 * 處理：那會產出一份沒有時數的草稿，Viewer 打錯字卻看到「成功」。
 */
export function WorklogDayEmpty({
  date,
  disabled = false,
  onGenerate,
}: {
  date: string
  disabled?: boolean
  onGenerate: (totalHours: number | null) => void
}) {
  const [hours, setHours] = useState('')
  const [invalid, setInvalid] = useState(false)
  const inputId = useId()

  const submit = () => {
    const parsed = parseTotalHours(hours)
    if (parsed === undefined) {
      setInvalid(true)
      return
    }
    setInvalid(false)
    onGenerate(parsed)
  }

  return (
    <section aria-label="尚無草稿" className="max-w-prose">
      <p className="text-fg-dim text-sm leading-relaxed">
        {date} 還沒有工時草稿。產生時會從你的 git commit 與 Chat 發言整理出這天做了什麼、分成幾群，
        並附上候選的 ZPlanner issue——<strong className="font-medium">專案一律由你自己選</strong>，系統不會先幫你選好。
      </p>

      <form
        className="mt-4 flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          submit()
        }}
      >
        <div className="flex flex-col gap-1">
          <label htmlFor={inputId} className="text-fg-dim text-xs">
            當天總時數（可先不填）
          </label>
          <Input
            id={inputId}
            aria-label="當天總時數"
            inputMode="decimal"
            placeholder="例如 8"
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
        <Button type="submit" disabled={disabled}>
          產生草稿
        </Button>
      </form>

      {invalid ? (
        <p role="alert" className="text-destructive mt-2 text-xs">
          總時數要是大於 0 的數字（例如 8 或 7.5），或整個留白不分配。
        </p>
      ) : null}

      <p className="text-fg-subtle mt-4 text-xs leading-relaxed">
        填了總時數，系統會依證據比例分配成 0.5 的倍數；之後每一筆都還可以自己改。
        還沒有登錄任何工作 repo 的話，先到設定中心加一個——沒有 repo 就沒有 commit 可以當證據。
      </p>
    </section>
  )
}
