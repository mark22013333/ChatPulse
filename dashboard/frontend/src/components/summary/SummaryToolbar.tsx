import { useState } from 'react'
import { Loader2Icon, SparklesIcon, SquareIcon, WandSparklesIcon } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ProviderSelect } from '@/components/ProviderSelect'
import { api, errorMessage } from '@/lib/api'
import { useSummaryStore } from '@/store/summary'
import { useDraftStore } from '@/store/draft'
import { useMentionsStore } from '@/store/mentions'
import type { Space, SummaryStyleValue } from '@/lib/types'

interface SummaryToolbarProps {
  space: Space | null
  /** 草稿目標建立好、已開始生成時呼叫，由外層導航到草稿工作區 */
  onDraftCreated?: (mentionId: number) => void
}

/**
 * 摘要工作台的工具列：目標 Space、風格、供應商、抓取則數、產生回覆草稿、
 * 開始／停止（規格 §9.2）。
 *
 * 自己用細 selector 讀 store（§9.3），不從 `SummaryWorkspace` 接一包 props
 * ——這一列訂閱的都是設定值與布林，不會被串流的每個 chunk 帶著重繪。
 */
export function SummaryToolbar({ space, onDraftCreated }: SummaryToolbarProps) {
  const styles = useSummaryStore((s) => s.styles)
  const style = useSummaryStore((s) => s.style)
  const limit = useSummaryStore((s) => s.limit)
  const limitError = useSummaryStore((s) => s.limitError)
  const streaming = useSummaryStore((s) => s.streaming)
  const hasText = useSummaryStore((s) => s.text.length > 0)
  const setStyle = useSummaryStore((s) => s.setStyle)
  const setLimit = useSummaryStore((s) => s.setLimit)
  const start = useSummaryStore((s) => s.start)
  const abort = useSummaryStore((s) => s.abort)

  const selectExternal = useMentionsStore((s) => s.selectExternal)
  const generateDraft = useDraftStore((s) => s.generate)

  const [draftingReply, setDraftingReply] = useState(false)

  const canStart = Boolean(space) && !streaming && !limitError
  const styleItems = Object.fromEntries(styles.map((item) => [item.value, item.label]))

  /**
   * 對這個對話產生回覆草稿。
   *
   * 私訊不會出現在 Mention 收件匣（沒人會在私訊裡 @ 你），所以草稿流程本來
   * 用不到。這裡請後端挑出「對方最後說的那則」並合成一個草稿目標，
   * 拿到之後就走原本那條草稿串流——不必為私訊另寫一份邏輯。
   */
  const handleDraftReply = async () => {
    if (!space) return
    setDraftingReply(true)
    try {
      const res = await api.createDraftTarget({ space_id: space.id })
      if (!res.mention) {
        toast.error('這個對話裡找不到別人發的訊息，沒有東西可以回覆')
        return
      }
      selectExternal(res.mention)
      void generateDraft(res.mention.id) // 不等它跑完，切過去就看得到串流
      onDraftCreated?.(res.mention.id)
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setDraftingReply(false)
    }
  }

  /**
   * 把目前的抓取則數記成個人預設。
   *
   * 失敗只寫 console 不打擾使用者——這是順手記住的便利功能，
   * 存不進去頂多下次要再改一次，不值得用一個錯誤提示打斷他。
   */
  const rememberLimit = async () => {
    if (limitError || !Number.isInteger(limit)) return
    try {
      await api.updatePreferences({ default_limit: limit })
    } catch (err) {
      console.warn('抓取則數沒能存成預設：', err)
    }
  }

  return (
    // bg-raised：工具列是浮在內容之上的操作面，不是內容本身。
    // 改版前這裡只靠底部一條 border 分隔（規線分群），現在多一階填色差——
    // 那正是「後台感」的來源：先看得出有幾層，才看得懂哪一層在做什麼。
    <div className="flex shrink-0 flex-wrap items-end gap-3 border-b border-border bg-raised px-5 py-3">
      <div className="mr-auto min-w-0">
        <p className="truncate text-sm font-semibold">
          {space ? space.displayName : '尚未選擇 Space'}
        </p>
        <p className="metric truncate text-xs text-muted-foreground">
          {space ? space.id : '請從左側清單選一個 Space'}
        </p>
      </div>

      <div className="flex flex-col gap-1">
        <Label htmlFor="summary-style" className="text-xs text-muted-foreground">
          摘要風格
        </Label>
        <Select
          items={styleItems}
          value={style}
          onValueChange={(value) => {
            // Select 清除選擇時會給 null，那種情況不要動偏好
            if (!value) return
            setStyle(value as SummaryStyleValue)
            // 與抓取則數同理：選了就記住，不必每次重選
            void api
              .updatePreferences({ default_style: value })
              .catch((err) => console.warn('摘要風格沒能存成預設：', err))
          }}
        >
          <SelectTrigger id="summary-style" size="sm" className="w-32">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {styles.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <ProviderSelect id="summary-provider" disabled={streaming} />

      <div className="flex flex-col gap-1">
        <Label htmlFor="summary-limit" className="text-xs text-muted-foreground">
          抓取則數（1~1000）
        </Label>
        <Input
          id="summary-limit"
          type="number"
          min={1}
          max={1000}
          value={Number.isNaN(limit) ? '' : limit}
          onChange={(event) => setLimit(event.target.value)}
          // 離開輸入框時把值記成個人預設。以前改了只影響這一次，下次開啟
          // 又跳回舊值——使用者得每次重打，那不叫「預設」。
          onBlur={() => void rememberLimit()}
          className="h-7 w-28"
          aria-invalid={Boolean(limitError)}
          aria-describedby="summary-limit-hint"
        />
        {/* 「改完會被記住」原本只活在 tooltip 裡。那是這個欄位的**行為**
            ——使用者不知道的話會以為只影響這一次（規格 §10.3）。 */}
        <p id="summary-limit-hint" className="text-2xs text-muted-foreground">
          改完離開欄位就會記住，下次開啟直接用這個值
        </p>
      </div>

      {/* 產生回覆草稿放在這裡而不是摘要結果區——私訊的重點常常就是「怎麼回」，
          不該逼使用者先跑一次摘要才拿得到草稿。選了 Space 就能按。 */}
      <div className="flex flex-col gap-0.5">
        <Button
          variant="outline"
          onClick={() => void handleDraftReply()}
          disabled={!space || streaming || draftingReply}
          aria-describedby="draft-reply-hint"
        >
          {draftingReply ? <Loader2Icon className="animate-spin" /> : <WandSparklesIcon />}
          產生回覆草稿
        </Button>
        {/* 這顆按鈕實際做什麼是唯一資訊——「產生回覆草稿」四個字看不出
            它挑的是「對方最後說的那句」。原本只活在 tooltip 裡（§10.3）。 */}
        <p id="draft-reply-hint" className="text-2xs text-muted-foreground">
          針對對方最後說的話
        </p>
      </div>

      {streaming ? (
        <Button variant="outline" onClick={abort}>
          <SquareIcon />
          停止串流
        </Button>
      ) : (
        <Button disabled={!canStart} onClick={() => space && void start(space.id)}>
          <SparklesIcon />
          {hasText ? '重新摘要' : '開始摘要'}
        </Button>
      )}
    </div>
  )
}
