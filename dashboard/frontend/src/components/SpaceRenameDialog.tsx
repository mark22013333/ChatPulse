import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { onEnter } from '@/lib/keyboard'
import { useSpacesStore } from '@/store/spaces'
import type { Space } from '@/lib/types'

/**
 * 給私訊／未命名的 Space 取別名。
 *
 * 從舊的 Space 導覽欄原樣抽出來：摘要工作台改成表格（`SpacesTable`）的過渡期
 * 兩邊都要用，複製一份的話「自動猜的名字不預填」這種細節遲早只改到一邊。
 * 舊導覽欄移除後，目前只有 `SpacesTable` 使用。
 */
export function useSpaceRename() {
  const rename = useSpacesStore((state) => state.rename)
  // 正在改名的空間；null＝對話框關著
  const [renaming, setRenaming] = useState<Space | null>(null)
  const [aliasDraft, setAliasDraft] = useState('')

  const openRename = (space: Space) => {
    // 自動猜的名字不預填——那是猜的，讓使用者從空白開始比較清楚；
    // 自己取過的才預填，方便微調。
    setAliasDraft(space.nameSource === 'dm_manual' ? space.displayName : '')
    setRenaming(space)
  }

  const submitRename = async () => {
    if (!renaming) return
    await rename(renaming.id, aliasDraft)
    setRenaming(null)
  }

  const dialog = (
    <Dialog open={renaming !== null} onOpenChange={(open) => !open && setRenaming(null)}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>為這個空間取個名字</DialogTitle>
          <DialogDescription>
            私訊在 Google Chat 沒有名稱，我們只能從訊息裡認出對方是誰——
            對方沒在任何群組被 @ 過就認不出來。你知道他是誰，直接取一個好認的名字。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="space-alias">名字</Label>
          <Input
            id="space-alias"
            value={aliasDraft}
            autoFocus
            maxLength={60}
            placeholder="例如：王小明（某某廠商）"
            onChange={(e) => setAliasDraft(e.target.value)}
            // onEnter 會擋掉輸入法組字中的 Enter——注音選字時按 Enter
            // 是「選這個字」，不是「儲存」。見 lib/keyboard.ts
            onKeyDown={onEnter(() => void submitRename())}
          />
          <p className="text-xs text-muted-foreground">
            清空後儲存＝取消自訂，回到自動辨識的結果。這個名字只有你看得到，
            不會改動 Google Chat。
          </p>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => setRenaming(null)}>
            取消
          </Button>
          <Button onClick={() => void submitRename()}>儲存</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )

  return { openRename, dialog }
}
