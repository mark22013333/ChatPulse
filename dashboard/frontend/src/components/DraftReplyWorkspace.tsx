import { useEffect, useMemo, useState } from 'react'
import {
  AlertCircleIcon,
  CompassIcon,
  FileCodeIcon,
  MessageSquareQuoteIcon,
  SendIcon,
  SparklesIcon,
  SquareIcon,
  XIcon,
} from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { Markdown } from '@/components/Markdown'
import { ProviderSelect } from '@/components/ProviderSelect'
import { QuickReplySettings } from '@/components/draft/QuickReplySettings'
import { SpaceList } from '@/components/SpaceList'
import { errorMessage } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { splitDraft, useDraftStore } from '@/store/draft'
import { ENV_LABELS, ENV_ORDER, useCodeProjectStore } from '@/store/codeProjects'
import { useMentionsStore } from '@/store/mentions'
import { filterSpaces, useSpacesStore } from '@/store/spaces'
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
 * Draft Reply 工作區（規格 7 節）。
 * Reference Space 預設一個都不勾（7.3），送出前一定要二次確認（7.2 步驟 6）。
 */
export function DraftReplyWorkspace({ mention, active = true }: DraftReplyWorkspaceProps) {
  const spaces = useSpacesStore((state) => state.items)
  const applyResolved = useMentionsStore((state) => state.applyResolved)
  const applyResolvedMany = useMentionsStore((state) => state.applyResolvedMany)
  const mergeIds = useMentionsStore((state) => state.mergeIds)

  const {
    referenceSpaceIds,
    referenceSearch,
    refLimit,
    refLimitError,
    streaming,
    raw,
    meta,
    error,
    polish,
    replyText,
    sending,
    toggleReference,
    clearReferences,
    codeRefs,
    toggleCodeRef,
    clearCodeRefs,
    setReferenceSearch,
    setRefLimit,
    setReplyText,
    generate,
    abort,
    reset,
  } = useDraftStore()

  const [confirmOpen, setConfirmOpen] = useState(false)

  // 參考專案（ADR-0006）。與 Reference Space 一樣預設不勾。
  const codeProjects = useCodeProjectStore((s) => s.projects)
  const loadCodeProjects = useCodeProjectStore((s) => s.ensureLoaded)
  useEffect(() => {
    void loadCodeProjects()
  }, [loadCodeProjects])

  // store 記著「目前這份草稿是誰的」，用它判斷要不要清空，元件自己不必追蹤
  const streamedMentionId = useDraftStore((state) => state.mentionId)

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

  // 這裡刻意**不**在卸載時 abort。
  //
  // 串流狀態全部住在 store，元件只是畫面；卸載就中止等於「切個頁籤就把
  // 已經燒掉的 AI 額度丟掉」，而且後端要整段跑完才落盤（server.py 的
  // create_draft），內容會一起消失。要停止請按畫面上的停止鍵——那才是
  // 使用者明確表達的意圖。

  const sections = useMemo(() => splitDraft(raw), [raw])
  // 只有「這一則自己也在勾選裡」時才算在合併——否則收件匣勾了 A、B，
  // 使用者卻點開 C 去按產生，會把不相干的 A、B 一起回掉
  const activeMergeIds = useMemo(
    () => (mention && mergeIds.includes(mention.id) ? mergeIds : []),
    [mergeIds, mention],
  )
  const referenceCandidates = useMemo(
    () => filterSpaces(spaces, referenceSearch),
    [spaces, referenceSearch],
  )
  const selectedNames = useMemo(
    () =>
      referenceSpaceIds
        .map((id) => spaces.find((space) => space.id === id)?.displayName ?? id)
        .filter(Boolean),
    [referenceSpaceIds, spaces],
  )

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

  const handleSend = async () => {
    try {
      const updated = await useDraftStore.getState().send(mention.id)
      if (updated.length) applyResolvedMany(updated)
      else applyResolved({ ...mention, state: 'resolved', resolved_at: new Date().toISOString() })
      toast.success(
        updated.length > 1
          ? `已送出回話，這 ${updated.length} 則都標記為已處理`
          : '已送出回話，該則 Mention 已標記為已處理',
      )
      setConfirmOpen(false)
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 原始 Mention */}
      <div className="shrink-0 border-b border-border px-5 py-3">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="text-sm font-semibold">{mention.space_name}</span>
          <span className="text-xs text-muted-foreground">
            {mention.sender_display} · {formatDateTime(mention.create_time)}
          </span>
          {/* manual 必須單獨標。以前這裡只分 pending 與「其他」，於是從摘要
              工作台挑來的草稿目標被顯示成「已處理」——但它不在「已處理」清單裡
              （那個分頁查的是 resolved），使用者會以為系統漏掉了他的紀錄。 */}
          {/*
            三種狀態不能只靠顏色分辨（設計原則 3）。`--verified` 與 `--signal`
            刻意是同一個色，所以這裡改成：**只有需要動作的「待處理」帶訊號色**，
            另外兩種安靜下來，再用 ✓ 與文字把「已處理」和「自選對話」分開。
          */}
          <span
            className={cn(
              'rounded border px-1.5 py-0.5 text-2xs',
              mention.state === 'pending'
                ? 'border-signal-line bg-signal-wash text-signal'
                : 'border-border bg-muted text-fg-dim',
            )}
          >
            {mention.state === 'pending'
              ? '待處理'
              : mention.state === 'manual'
                ? '自選對話'
                : '✓ 已處理'}
          </span>
          {/* 這段說明原本只活在 badge 的 title 屬性裡，鍵盤與觸控使用者拿不到 */}
          {mention.state === 'manual' ? (
            <span className="text-fg-dim text-2xs">
              你從摘要工作台挑的對話，不是別人 @ 你，所以不在收件匣的待辦清單裡。送出回話後會歸到「已處理」。
            </span>
          ) : null}
        </div>
        <p className="mt-2 rounded-lg border border-border bg-muted/40 p-3 text-xs leading-relaxed whitespace-pre-wrap">
          {mention.text ?? `（無法取回訊息內容${mention.content_error ? `：${mention.content_error}` : ''}）`}
        </p>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[280px_1fr]">
        {/* Reference Space 勾選 */}
        <div className="flex min-h-0 flex-col border-b border-border xl:border-r xl:border-b-0">
          <div className="shrink-0 space-y-2 px-3 py-2.5">
            <div className="flex items-center gap-2">
              <h3 className="text-xs font-semibold">Reference Space</h3>
              <span className="text-2xs text-muted-foreground">
                已勾選 {referenceSpaceIds.length}
              </span>
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
                />
              </div>
            </div>
            {refLimitError ? (
              <p className="text-2xs text-destructive">{refLimitError}</p>
            ) : null}

            <ProviderSelect id="draft-provider" disabled={streaming} triggerClassName="w-full" />
          </div>

          {/* 回覆設定（ADR-0007）。放在供應商之後、資料來源之前，
              維持「模型與生成設定在上、資料來源在下」的既有分組。 */}
          <QuickReplySettings disabled={streaming} />

          <SpaceList
            spaces={referenceCandidates}
            label="一起當作參考的 Space"
            checkedIds={referenceSpaceIds}
            onToggle={(space) => toggleReference(space.id)}
            emptyHint="查無符合的 Space"
            className="max-h-64 xl:max-h-none"
          />

          {/* 參考專案（ADR-0006）。勾一個專案的兩個環境＝比對正式與 UAT。 */}
          {codeProjects.length > 0 ? (
            <div className="shrink-0 space-y-2 border-t border-border px-3 py-2.5">
              <div className="flex items-center gap-2">
                <h3 className="flex items-center gap-1.5 text-xs font-semibold">
                  <FileCodeIcon className="size-3.5" aria-hidden />
                  參考專案
                </h3>
                <span className="text-2xs text-muted-foreground">
                  已勾選 {codeRefs.length}
                </span>
                {codeRefs.length > 0 ? (
                  <Button size="xs" variant="ghost" className="ml-auto" onClick={clearCodeRefs}>
                    <XIcon />
                    清空
                  </Button>
                ) : null}
              </div>
              <p className="text-2xs leading-relaxed text-muted-foreground">
                同一個專案可同時勾正式與 UAT，草稿會分開講兩邊的差異。
              </p>
              <ul className="space-y-1.5">
                {codeProjects.map((p) => (
                  <li key={p.id} className="space-y-1">
                    <p className="truncate text-xs font-medium">{p.name}</p>
                    <div className="flex flex-wrap gap-1">
                      {ENV_ORDER.filter((env) => p.branches[env]).map((env) => {
                        const checked = codeRefs.some(
                          (r) => r.project_id === p.id && r.environment === env,
                        )
                        return (
                          <label
                            key={env}
                            className={`flex cursor-pointer items-center gap-1 rounded border px-1.5 py-0.5 text-2xs ${
                              checked
                                ? 'border-primary bg-primary/10 text-primary'
                                : 'border-border text-muted-foreground'
                            }`}
                          >
                            <input
                              type="checkbox"
                              className="sr-only"
                              checked={checked}
                              disabled={streaming}
                              onChange={() => toggleCodeRef(p.id, env)}
                            />
                            {ENV_LABELS[env]}
                            <code className="metric opacity-70">{p.branches[env]}</code>
                          </label>
                        )
                      })}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="shrink-0 border-t border-border p-2.5">
            {streaming ? (
              <Button variant="outline" className="w-full" onClick={abort}>
                <SquareIcon />
                停止串流
              </Button>
            ) : (
              <Button
                className="w-full"
                // 收件匣勾了要合併的話，這顆按鈕也要照著合併——不然
                // 「勾了兩則卻只回到一則」是靜默的，使用者要送出後才發現
                onClick={() => void generate(mention.id, activeMergeIds)}
                disabled={Boolean(refLimitError)}
              >
                <SparklesIcon />
                {activeMergeIds.length > 1
                  ? `${raw ? '重新產生' : '產生'} Draft Reply（合併 ${activeMergeIds.length} 則）`
                  : raw
                    ? '重新產生 Draft Reply'
                    : '產生 Draft Reply'}
              </Button>
            )}
          </div>
        </div>

        {/* 產出：脈絡分析 + 建議回話 */}
        <div className="min-h-0 overflow-y-auto p-5">
          {error ? (
            <div className="mb-4 flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
              <AlertCircleIcon className="mt-0.5 size-3.5 shrink-0" />
              <span className="leading-relaxed">{error}</span>
            </div>
          ) : null}

          {!raw && !streaming && !error ? (
            <p className="py-10 text-center text-xs text-muted-foreground">
              勾好 Reference Space 之後按「產生 Draft Reply」。草稿永遠只是草稿，一定要你看過、改過、確認後才會送出。
            </p>
          ) : null}

          {raw || streaming ? (
            <div className="space-y-4">
              {/* 這裡原本是一排會換行的彩色小藥丸：脈絡則數、合併幾則、圖片幾張、
                  供應商、口氣、Persona、自訂提示、Sepia 狀態、每個 Reference Space，
                  全部 11px、四色混雜、位置隨內容浮動。它們現在住在右側的證據欄，
                  有固定的位置與順序（設計規格 §5）。 */}

              {/*
                潤稿被退回時要明說原因。只放一個琥珀 badge 不夠——
                使用者需要知道「是哪個事實被改動了」，那是判斷「模型在亂改」
                還是「檢查太嚴」的唯一依據。
              */}
              {polish && !polish.polished && polish.fallback_reason ? (
                <div className="mt-2 rounded border border-caution-line bg-caution/10 px-3 py-2 text-xs leading-relaxed text-caution">
                  <span className="font-medium">Sepia 潤稿未採用</span>
                  <span className="ml-1">{polish.fallback_reason}</span>
                  <span className="ml-1 text-muted-foreground">
                    下面顯示的是未潤稿的版本，內容仍然可以直接送出。
                  </span>
                </div>
              ) : null}

              {/* 參考專案的檢索結果（分支、commit、命中檔案）現在是證據欄的
                  一組列。它本來就是「這份草稿建立在什麼之上」的一部分，放在
                  固定位置比夾在生成內容中間更容易一眼掃過。 */}

              <section>
                <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold">
                  <CompassIcon className="size-4 text-signal" />
                  脈絡分析
                </h3>
                <Markdown
                  source={sections.context}
                  typing={streaming && !sections.replyStarted}
                  active={active}
                  className="rounded-xl border border-border bg-card/60 p-4"
                />
              </section>

              <section>
                <div className="mb-2 flex items-center gap-1.5">
                  <MessageSquareQuoteIcon className="size-4 text-verified" />
                  <h3 className="text-sm font-semibold">建議回話</h3>
                  <span className="text-xs text-muted-foreground">（可直接編輯）</span>
                  <Button
                    size="sm"
                    className="ml-auto"
                    onClick={() => setConfirmOpen(true)}
                    disabled={streaming || sending || !replyText.trim()}
                  >
                    <SendIcon />
                    送出回話
                  </Button>
                </div>
                <Textarea
                  value={replyText}
                  onChange={(event) => setReplyText(event.target.value)}
                  rows={10}
                  placeholder="建議回話會串流到這裡，你可以直接修改。"
                  // 這是要給人讀的中文散文，不是 log：等寬對 CJK 沒作用，只會讓它難讀
                  className="min-h-48 text-base leading-relaxed"
                />
              </section>
            </div>
          ) : null}
        </div>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="送出這則回話？"
        description={
          <>
            將以<strong className="text-foreground">你本人的身分</strong>送出，並回到原討論串（
            <strong className="text-foreground">{mention.space_name}</strong>）。
            {(meta?.answering?.length ?? 0) > 1 ? (
              <>
                {' '}
                只會送出<strong className="text-foreground">這一則</strong>訊息，
                但送出後下面這 <strong className="text-foreground">
                  {meta?.answering?.length} 則
                </strong>
                會一起標成已處理——送出前請確認回話真的每一則都回到了。
                {/* 逐則列出來，不是只講數字。這是整個流程裡最需要看清楚的一刻：
                    送出不可撤回，而「漏回其中一則」從草稿內容本身看不出來。
                    在此之前這份清單只活在一個 title 屬性裡。 */}
                <ul className="mt-2 space-y-0.5">
                  {(meta?.answering ?? []).map((item) => (
                    <li key={item.mention_id} className="flex justify-between gap-3 text-xs">
                      <span className="text-foreground">{item.sender_display ?? '未知成員'}</span>
                      <span className="metric text-muted-foreground">
                        {item.create_time ? formatDateTime(item.create_time) : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <> 送出後這則 Mention 會自動變成已處理。</>
            )}
            {selectedNames.length > 0 ? (
              <>
                <br />
                本次參考的 Reference Space：{selectedNames.join('、')}
              </>
            ) : null}
          </>
        }
        preview={replyText}
        previewLabel="回話全文預覽"
        confirmLabel="確認送出"
        pending={sending}
        onConfirm={() => void handleSend()}
      />
    </div>
  )
}
