import { XIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ProviderSelect } from '@/components/ProviderSelect'
import { useDraftStore } from '@/store/draft'

/**
 * Reference Space 的控件：搜尋、每群抓取則數、供應商（規格 §9.2）。
 *
 * 清單本身（`SpaceList`）不在這裡——它與這張卡之間夾著 `QuickReplySettings`，
 * 由 `DraftSetupPanel` 依序組裝。那個順序是刻意的（見它的註解）：
 * 模型與生成設定在上、資料來源在下。
 *
 * 供應商選單留在這張卡裡，因為它在畫面上就是這張卡的最後一列；
 * 為了讓檔名與規格的名字對得更齊而把它抽走，只會讓 DOM 與檔案結構不一致。
 *
 * 每個值都用細 selector 讀（規格 §9.3）。**不要改回無 selector 的
 * `useDraftStore()`**：那樣每個串流 chunk 都會重繪這整棵子樹，包含下面
 * 436 筆的虛擬清單——那正是改版要修掉的效能問題。
 */
export function ReferenceSpacePicker() {
  const referenceSpaceIds = useDraftStore((s) => s.referenceSpaceIds)
  const referenceSearch = useDraftStore((s) => s.referenceSearch)
  const refLimit = useDraftStore((s) => s.refLimit)
  const refLimitError = useDraftStore((s) => s.refLimitError)
  const streaming = useDraftStore((s) => s.streaming)
  const clearReferences = useDraftStore((s) => s.clearReferences)
  const setReferenceSearch = useDraftStore((s) => s.setReferenceSearch)
  const setRefLimit = useDraftStore((s) => s.setRefLimit)

  return (
    <div className="shrink-0 space-y-2 px-3 py-2.5">
      <div className="flex items-center gap-2">
        <h3 className="text-xs font-semibold">Reference Space</h3>
        <span className="text-2xs text-muted-foreground">已勾選 {referenceSpaceIds.length}</span>
        {referenceSpaceIds.length > 0 ? (
          <Button size="xs" variant="ghost" className="ml-auto" onClick={clearReferences}>
            <XIcon />
            清空
          </Button>
        ) : null}
      </div>
      <p className="text-2xs leading-relaxed text-muted-foreground">
        預設一個都不勾。勾選的 Space 近期訊息會一併送進脈絡。
      </p>
      <Input
        value={referenceSearch}
        onChange={(event) => setReferenceSearch(event.target.value)}
        placeholder="搜尋 Space 名稱…"
        className="h-7"
      />
      <div className="flex items-end gap-2">
        <div className="flex-1 space-y-1">
          <Label htmlFor="draft-limit" className="text-2xs text-muted-foreground">
            每群抓取則數（1~1000）
          </Label>
          <Input
            id="draft-limit"
            type="number"
            min={1}
            max={1000}
            value={Number.isNaN(refLimit) ? '' : refLimit}
            onChange={(event) => setRefLimit(event.target.value)}
            className="h-7"
            aria-invalid={Boolean(refLimitError)}
            aria-describedby={refLimitError ? 'draft-limit-error' : undefined}
          />
        </div>
      </div>
      {refLimitError ? (
        // role="alert" ＋ aria-describedby：只有 aria-invalid 的話，螢幕閱讀器
        // 讀得出「這欄有問題」但讀不到「問題是什麼」（規格 §10.7）
        <p id="draft-limit-error" role="alert" className="text-2xs text-destructive">
          {refLimitError}
        </p>
      ) : null}

      <ProviderSelect id="draft-provider" disabled={streaming} triggerClassName="w-full" />
    </div>
  )
}
