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
    /*
      **整欄是一個 raised 的操作面**（底色與邊界由 `DraftReplyWorkspace` 的側欄
      容器一次宣告，這裡只負責堆疊）。內部一律是水平帶：Reference Space、
      回覆設定、Space 清單、參考專案各佔一條，彼此用一條細線隔開。

      **不要把每一區包成卡片**——它們是同一組設定的分組，不是四份各自獨立的
      產出；包起來就變成盒子套盒子，而且會和產出區真正的卡片搶同一個視覺頻道。
    */
    <div className="flex min-h-0 flex-1 flex-col">
      {/*
        設定堆疊自己要有捲軸（2026-09-10 使用者實機回報的 bug）。

        在此之前這一欄是「固定高度 ＋ 一堆 shrink-0 的塊」，沒有任何一層可捲：
        1440×900 實測四塊固定內容共 **989px**，而欄高只有 **748px**。溢出的
        251px 沒有人捲得到，被 `AppShell` 的 `overflow-hidden` 直接裁掉
        ——排在最後的產生鈕整顆落在裁切線外，**功能等於不能用**。

        **外層不能救**：`AppShell` 是 `h-full` ＋ `overflow-hidden`，而 `index.css`
        把 `html`／`body` 鎖成不可捲——document 永遠不捲是三欄工作台的前提。
        所以捲軸必須在這一層。
      */}
      <div className="min-h-0 flex-1 overflow-y-auto">
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
          /*
            **一定要給確定的高度，不能留 `flex-1`。** `SpaceList` 內建
            `flex-1 overflow-y-auto`（`SpaceList.tsx:124`）——那是為 `SpacesRail`
            設計的「吃掉剩餘空間」。搬到這個捲動容器裡就成立不了：`basis-0`
            仍然對容器高度解析，上面兩塊固定內容一多，它就被壓到剩下一條縫。
            實測**只有 8px、而內容是 22708px**，使用者因此完全看不到 Reference
            Space 清單，也不會意識到自己漏勾了什麼。

            舊寫法 `max-h-64 xl:max-h-none` 修不了這件事：`max-height` 只設上限，
            被壓扁時它一點作用也沒有；而 `xl:max-h-none` 更是把 ≥1280 唯一的
            高度線索也拿掉。`cn` 是 tailwind-merge 的替代品，所以 `flex-none`
            覆寫得掉元件內建的 `flex-1`。
          */
          className="h-64 flex-none"
        />

        <CodeRefPicker />
      </div>

      {/*
        **產生鈕刻意留在捲動區外面**，永遠釘在欄底。

        它是這一欄唯一的主要動作，而且是「開始燒 AI 配額」的那一步；放進捲動
        區的話，使用者每次都要先捲到底才找得到它——那正是這次 bug 的使用者
        體感（「我根本按不到產生草稿」）。釘住之後，設定捲到哪裡它都在。
      */}
      <GenerateButton mention={mention} />
    </div>
  )
}
