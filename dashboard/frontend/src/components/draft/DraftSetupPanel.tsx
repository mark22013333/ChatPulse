import { useMemo } from 'react'
import { SpaceList } from '@/components/SpaceList'
import { CodeRefPicker } from '@/components/draft/CodeRefPicker'
import { GenerateButton } from '@/components/draft/GenerateButton'
import { QuickReplySettings } from '@/components/draft/QuickReplySettings'
import { ReferenceSpacePicker } from '@/components/draft/ReferenceSpacePicker'
import { useDraftStore } from '@/store/draft'
import { filterSpaces, useSpacesStore } from '@/store/spaces'
import type { Mention } from '@/lib/types'

/**
 * 草稿左欄：設定堆疊的排版容器（規格 §9.2）。
 *
 * **順序是刻意的**：Reference Space 的控件與供應商在最上，接著是回覆設定，
 * 再往下才是資料來源（Space 清單、參考專案），最後是產生鈕。判準是
 * 「模型與生成設定在上、資料來源在下」——與改版前的分組一致，不要重排。
 */
export function DraftSetupPanel({ mention }: { mention: Mention }) {
  const spaces = useSpacesStore((s) => s.items)
  const referenceSearch = useDraftStore((s) => s.referenceSearch)
  const referenceSpaceIds = useDraftStore((s) => s.referenceSpaceIds)
  const toggleReference = useDraftStore((s) => s.toggleReference)
  // 要用 selector 而不是 getState()：後者讀一次就不再訂閱，串流開始時這裡
  // 不會重繪，回覆設定就不會變成 disabled（使用者能在生成中改口氣）。
  // 訂閱它是安全的——streaming 一次生成只翻兩次，不是每個 chunk 都動。
  const streaming = useDraftStore((s) => s.streaming)

  const candidates = useMemo(() => filterSpaces(spaces, referenceSearch), [spaces, referenceSearch])

  return (
    <div className="flex min-h-0 flex-col border-b border-border xl:border-r xl:border-b-0">
      <ReferenceSpacePicker />

      {/* 回覆設定（ADR-0007）。放在供應商之後、資料來源之前，
          維持「模型與生成設定在上、資料來源在下」的既有分組。 */}
      <QuickReplySettings disabled={streaming} />

      <SpaceList
        spaces={candidates}
        label="一起當作參考的 Space"
        checkedIds={referenceSpaceIds}
        onToggle={(space) => toggleReference(space.id)}
        emptyHint="查無符合的 Space"
        className="max-h-64 xl:max-h-none"
      />

      <CodeRefPicker />

      <GenerateButton mention={mention} />
    </div>
  )
}
