import { useEffect, useState } from 'react'
import { MessageSquareQuoteIcon } from 'lucide-react'
import { DraftOutputPane } from '@/components/draft/DraftOutputPane'
import { DraftSetupPanel } from '@/components/draft/DraftSetupPanel'
import { MentionSourceCard } from '@/components/draft/MentionSourceCard'
import { SendReplyConfirm } from '@/components/draft/SendReplyConfirm'
import { useDraftStore } from '@/store/draft'
import type { Mention } from '@/lib/types'

interface DraftReplyWorkspaceProps {
  mention: Mention | null
  /**
   * 這個工作台目前看得見嗎。兩個工作台常駐掛載之後，看不見的那一半仍會收到
   * 每個串流 chunk；傳下去讓 Markdown 在不可見時暫停重新 parse（§7.3）。
   */
  active?: boolean
}

/**
 * Draft Reply 工作區（規格 7 節、§9.2）。
 *
 * 這個檔案只剩兩件事：**版面骨架**與**換 Mention 的 reset 契約**。其餘都在
 * 同目錄的兄弟檔，各自用細 selector 讀 store（規格 §9.3）。
 *
 * 拆檔前這裡是一個 478 行的檔案，而且用**無 selector 的 `useDraftStore()`**
 * 整包訂閱——每個串流 chunk 都重繪整棵子樹，包含 436 筆的虛擬清單。
 * **不要把那一行加回來**，那是這次改版要修掉的效能問題（規格 §9.3）。
 */
export function DraftReplyWorkspace({ mention, active = true }: DraftReplyWorkspaceProps) {
  const reset = useDraftStore((s) => s.reset)
  const loadStored = useDraftStore((s) => s.loadStored)
  // store 記著「目前這份草稿是誰的」，用它判斷要不要清空，元件自己不必追蹤
  const streamedMentionId = useDraftStore((s) => s.mentionId)

  const [confirmOpen, setConfirmOpen] = useState(false)

  // 只有**真的換了一則 Mention** 才清空。
  //
  // 以前這裡是無條件 reset()，而 App.tsx 的頁籤是條件渲染（不是隱藏），
  // 切頁籤會把這個元件整個卸載重掛——於是每次切回來，掛載時的 reset()
  // 就把還在串流的草稿清光了，使用者什麼都看不到。
  useEffect(() => {
    const id = mention?.id ?? null
    if (id !== null && streamedMentionId !== null && id !== streamedMentionId) {
      reset()
    }
  }, [mention?.id, streamedMentionId, reset])

  // 這一則如果有**存下來**的草稿就讀回來。
  //
  // 在這之前草稿只活在串流那一次的記憶體裡：重新整理、切回收件匣再點進來、
  // 或隔天再開，畫面都是空的，看起來像草稿沒了——實際上它一直在
  // `draft_replies` 裡（本機實測 53 筆）。
  //
  // 安全性靠 `loadStored` 自己的三道守衛：正在串流不讀、同一則已經有內容
  // 不讀、404 安靜略過。所以這個 effect 重複觸發是無害的。
  // 只在 active 時讀：背景那一半的 pane 仍然掛載著（`app/Pane.tsx`），
  // 不 gate 的話每次切頁籤都會為看不見的那一半多打一次。
  useEffect(() => {
    const id = mention?.id ?? null
    if (!active || id === null || !mention?.has_draft) return
    void loadStored(id)
  }, [active, mention?.id, mention?.has_draft, loadStored])

  // 這裡刻意**不**在卸載時 abort。
  //
  // 串流狀態全部住在 store，元件只是畫面；卸載就中止等於「切個頁籤就把
  // 已經燒掉的 AI 額度丟掉」，而且後端要整段跑完才落盤（server.py 的
  // create_draft），內容會一起消失。要停止請按畫面上的停止鍵——那才是
  // 使用者明確表達的意圖。

  if (!mention) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
        <span className="flex size-12 items-center justify-center rounded-full bg-muted text-signal">
          <MessageSquareQuoteIcon className="size-5" />
        </span>
        <div>
          <p className="text-sm font-medium">從左側收件匣點一則 Mention</p>
          <p className="mx-auto mt-1 max-w-sm text-xs text-muted-foreground">
            系統會取回該討論串的完整對話，你可以再勾選其他 Space 當作 Reference Space
            補充脈絡——被 @ 的問題，答案通常不在提問的那個 Space 裡。
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <MentionSourceCard mention={mention} />

      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[280px_1fr]">
        <DraftSetupPanel mention={mention} />
        <DraftOutputPane active={active} onRequestSend={() => setConfirmOpen(true)} />
      </div>

      {/* `confirmOpen` 住在這一層是因為它有兩個使用者：編輯器裡的送出鈕負責
          打開，對話框負責關。放進任一邊都得把 state 往上或往下穿。 */}
      <SendReplyConfirm mention={mention} open={confirmOpen} onOpenChange={setConfirmOpen} />
    </div>
  )
}
