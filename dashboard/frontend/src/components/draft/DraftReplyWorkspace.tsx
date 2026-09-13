import { useEffect, useRef, useState } from 'react'
import { ChevronLeftIcon, MessageSquareQuoteIcon, SlidersHorizontalIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { DraftOutputPane } from '@/components/draft/DraftOutputPane'
import { DraftSetupCollapsedBar } from '@/components/draft/DraftSetupCollapsedBar'
import { DraftSetupPanel } from '@/components/draft/DraftSetupPanel'
import { MentionSourceCard } from '@/components/draft/MentionSourceCard'
import { SendReplyConfirm } from '@/components/draft/SendReplyConfirm'
import { cn } from '@/lib/utils'
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
  const streaming = useDraftStore((s) => s.streaming)
  // 只訂閱「有沒有內容」這個布林，不訂閱 raw 本身——後者每個串流 chunk 都會變，
  // 訂了就等於讓整個工作區跟著每個 chunk 重繪（§9.3 要修掉的正是這件事）
  const hasDraft = useDraftStore((s) => s.raw.length > 0)

  const [confirmOpen, setConfirmOpen] = useState(false)

  // 設定側欄的收合。`userDecided` 記住「使用者自己表達過意見了」——
  // 一旦他手動展開或收合，自動行為就不再插手；自動收合不該推翻使用者
  // 剛剛做的決定。不必 persist：這是一次草稿作業期間的狀態。
  const [setupCollapsed, setSetupCollapsed] = useState(false)
  const [userDecided, setUserDecided] = useState(false)
  const wasStreaming = useRef(streaming)

  // 串流**結束**且真的產出了內容 → 自動收合設定側欄。
  //
  // 判準刻意是「streaming 由 true 翻成 false」而不是「現在有內容」：
  // 讀回既有草稿（loadStored）與測試直接塞 raw 都不經過串流，那些情境
  // 使用者並沒有剛按下產生，不該被收合突襲。
  useEffect(() => {
    const just = wasStreaming.current && !streaming
    wasStreaming.current = streaming
    if (just && hasDraft && !userDecided) setSetupCollapsed(true)
  }, [streaming, hasDraft, userDecided])

  // 換一則 Mention 就回到展開。
  //
  // 收合是「這一份草稿看完了」的狀態，不是使用者對整個工作台的偏好；
  // 而產生鈕就住在側欄裡，帶著收合狀態進到下一則會讓主要動作消失在
  // 一顆展開鈕後面。`userDecided` 一起歸零：新的一則是新的一次判斷。
  useEffect(() => {
    setSetupCollapsed(false)
    setUserDecided(false)
  }, [mention?.id])

  const toggleSetup = (collapsed: boolean) => {
    setUserDecided(true)
    setSetupCollapsed(collapsed)
  }

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

      {/* 收合後側欄變成這一條窄帶，設定摘要留在畫面上（見該元件的註解） */}
      {setupCollapsed ? <DraftSetupCollapsedBar onExpand={() => toggleSetup(false)} /> : null}

      <div
        className={cn(
          'grid min-h-0 flex-1 grid-cols-1',
          // 收合時整列只剩產出區：側欄那個 grid item 是 display:none，
          // 欄位模板也要跟著收掉，否則產出區會被塞進 280px 的第一軌
          !setupCollapsed && 'xl:grid-cols-[280px_1fr]',
        )}
      >
        {/*
          側欄收合是**藏起來不是卸載**：卸載會連帶丟掉回覆設定的展開狀態、
          並重跑一次 Persona／提示詞的載入，而那些都與「這欄現在看不看得見」
          無關。整欄的 raised 底色與邊界在這一層宣告一次。
        */}
        <div
          id="draft-setup-panel"
          className={
            setupCollapsed
              ? 'hidden'
              : 'flex min-h-0 flex-col border-b border-border bg-raised xl:border-r xl:border-b-0'
          }
        >
          <div className="flex shrink-0 items-center gap-1.5 border-b border-line px-3 py-2">
            <SlidersHorizontalIcon className="size-3.5 shrink-0" aria-hidden />
            <span className="text-xs font-semibold">草稿設定</span>
            <Button
              size="xs"
              variant="ghost"
              className="ml-auto"
              onClick={() => toggleSetup(true)}
              aria-expanded
              aria-controls="draft-setup-panel"
            >
              <ChevronLeftIcon />
              收合設定
            </Button>
          </div>
          <DraftSetupPanel mention={mention} />
        </div>

        <DraftOutputPane active={active} onRequestSend={() => setConfirmOpen(true)} />
      </div>

      {/* `confirmOpen` 住在這一層是因為它有兩個使用者：編輯器裡的送出鈕負責
          打開，對話框負責關。放進任一邊都得把 state 往上或往下穿。 */}
      <SendReplyConfirm mention={mention} open={confirmOpen} onOpenChange={setConfirmOpen} />
    </div>
  )
}
