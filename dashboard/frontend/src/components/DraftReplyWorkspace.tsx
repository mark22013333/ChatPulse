import { useEffect, useMemo, useState } from 'react'
import {
  AlertCircleIcon,
  CompassIcon,
  FileCodeIcon,
  Loader2Icon,
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
import { ReplySettings } from '@/components/ReplySettings'
import { SpaceList } from '@/components/SpaceList'
import { errorMessage } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { splitDraft, useDraftStore } from '@/store/draft'
import { ENV_LABELS, ENV_ORDER, useCodeProjectStore } from '@/store/codeProjects'
import { useMentionsStore } from '@/store/mentions'
import { providerLabel, useProviderStore } from '@/store/providers'
import { filterSpaces, useSpacesStore } from '@/store/spaces'
import type { DraftContextMeta, Mention } from '@/lib/types'

interface DraftReplyWorkspaceProps {
  mention: Mention | null
}

const CONTEXT_MODE_LABEL: Record<DraftContextMeta['mode'], string> = {
  flat_window: '前後脈絡',
  thread: '討論串',
  thread_thin: '討論串＋鄰近',
}

/** 把 meta.context 濃縮成一行。舊版後端沒有這個欄位，退回原本的「討論串 N 則」。 */
function contextLabel(context: DraftContextMeta | undefined, fallback: number | undefined) {
  if (!context) return `討論串 ${fallback ?? 0} 則`
  const label = CONTEXT_MODE_LABEL[context.mode] ?? '脈絡'
  const partial = context.coverage === 'partial' ? '（不連續）' : ''
  return `${label} ${context.message_count} 則${partial}`
}

/** hover 才需要看的細節：涵蓋的時間區間與各區塊的組成。 */
function contextTitle(context: DraftContextMeta | undefined) {
  if (!context) return '本次送進模型的討論串則數'
  const lines = context.blocks.map((b) => `${b.label}：${b.count} 則`)
  const { start, end } = context.time_range
  if (start && end) {
    lines.push(`涵蓋 ${formatDateTime(start)} ~ ${formatDateTime(end)}`)
  }
  if (context.coverage === 'partial') {
    lines.push('系統沒能取回這則訊息周圍的完整對話（它可能太舊了），脈絡不保證連續')
  }
  return lines.join('\n')
}

/**
 * Draft Reply 工作區（規格 7 節）。
 * Reference Space 預設一個都不勾（7.3），送出前一定要二次確認（7.2 步驟 6）。
 */
export function DraftReplyWorkspace({ mention }: DraftReplyWorkspaceProps) {
  const spaces = useSpacesStore((state) => state.items)
  const applyResolved = useMentionsStore((state) => state.applyResolved)
  const applyResolvedMany = useMentionsStore((state) => state.applyResolvedMany)
  const mergeIds = useMentionsStore((state) => state.mergeIds)
  const providers = useProviderStore((state) => state.providers)

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
  const loadCodeProjects = useCodeProjectStore((s) => s.load)
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
        <span className="flex size-12 items-center justify-center rounded-full bg-muted text-sky-500">
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
          <span
            className={cn(
              'rounded border px-1.5 py-0.5 text-[10px]',
              mention.state === 'pending' && 'border-sky-500/30 bg-sky-500/10 text-sky-500',
              mention.state === 'resolved' &&
                'border-emerald-500/30 bg-emerald-500/10 text-emerald-500',
              mention.state === 'manual' && 'border-border bg-muted text-muted-foreground',
            )}
            title={
              mention.state === 'manual'
                ? '你從摘要工作台挑的對話，不是別人 @ 你，所以不在收件匣的待辦清單裡。送出回話後會歸到「已處理」。'
                : undefined
            }
          >
            {mention.state === 'pending'
              ? '待處理'
              : mention.state === 'manual'
                ? '手動指定'
                : '已處理'}
          </span>
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
              <span className="text-[10px] text-muted-foreground">
                已勾選 {referenceSpaceIds.length}
              </span>
              {referenceSpaceIds.length > 0 ? (
                <Button size="xs" variant="ghost" className="ml-auto" onClick={clearReferences}>
                  <XIcon />
                  清空
                </Button>
              ) : null}
            </div>
            <p className="text-[10px] leading-relaxed text-muted-foreground">
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
                <Label htmlFor="draft-limit" className="text-[10px] text-muted-foreground">
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
              <p className="text-[10px] text-destructive">{refLimitError}</p>
            ) : null}

            <ProviderSelect id="draft-provider" disabled={streaming} triggerClassName="w-full" />
          </div>

          {/* 回覆設定（ADR-0007）。放在供應商之後、資料來源之前，
              維持「模型與生成設定在上、資料來源在下」的既有分組。 */}
          <ReplySettings disabled={streaming} />

          <SpaceList
            spaces={referenceCandidates}
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
                <span className="text-[10px] text-muted-foreground">
                  已勾選 {codeRefs.length}
                </span>
                {codeRefs.length > 0 ? (
                  <Button size="xs" variant="ghost" className="ml-auto" onClick={clearCodeRefs}>
                    <XIcon />
                    清空
                  </Button>
                ) : null}
              </div>
              <p className="text-[10px] leading-relaxed text-muted-foreground">
                同一個專案可同時勾正式與 UAT，草稿會分開講兩邊的差異。
              </p>
              <ul className="space-y-1.5">
                {codeProjects.map((p) => (
                  <li key={p.id} className="space-y-1">
                    <p className="truncate text-[11px] font-medium">{p.name}</p>
                    <div className="flex flex-wrap gap-1">
                      {ENV_ORDER.filter((env) => p.branches[env]).map((env) => {
                        const checked = codeRefs.some(
                          (r) => r.project_id === p.id && r.environment === env,
                        )
                        return (
                          <label
                            key={env}
                            className={`flex cursor-pointer items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] ${
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
                            <code className="font-mono opacity-70">{p.branches[env]}</code>
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
              {meta ? (
                <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                  {/* 脈絡的「形狀」一定要顯示：私訊走前後窗、群組走討論串，
                      涵蓋範圍差很多，而從草稿內容完全看不出來是哪一種。
                      在此之前這裡只寫「討論串 N 則」，私訊永遠顯示 1 則也沒人看得懂為什麼。 */}
                  <span
                    className={cn(
                      'font-mono',
                      meta.context?.coverage === 'partial' && 'text-amber-600 dark:text-amber-400',
                    )}
                    title={contextTitle(meta.context)}
                  >
                    {contextLabel(meta.context, meta.thread_message_count)}
                  </span>
                  {/* 合併回覆時一定要顯示「這份草稿會回掉幾則」：從草稿內容
                      看不出來它有沒有真的回到每一則，而送出會一次結掉全部。 */}
                  {(meta.answering?.length ?? 0) > 1 ? (
                    <span
                      className="rounded border border-sky-500/30 bg-sky-500/10 px-1.5 py-0.5 font-medium text-sky-600 dark:text-sky-400"
                      title={(meta.answering ?? [])
                        .map(
                          (a) =>
                            `${a.sender_display ?? '未知成員'} · ${formatDateTime(a.create_time)}`,
                        )
                        .join('\n')}
                    >
                      合併回覆 {meta.answering?.length} 則
                    </span>
                  ) : null}
                  {/* 圖片張數一定要顯示：附件有沒有被讀進去，從草稿內容看不出來，
                      使用者只能猜。顯示 0 張也有意義——那代表「讀了但沒有圖」。 */}
                  {meta.image_count !== undefined ? (
                    <span
                      className="font-mono"
                      title={
                        meta.images_skipped?.length
                          ? `略過：${meta.images_skipped.join('、')}`
                          : '實際送進模型的圖片張數'
                      }
                    >
                      · 圖片 {meta.image_count} 張
                      {meta.images_skipped?.length ? `（略過 ${meta.images_skipped.length}）` : ''}
                    </span>
                  ) : null}
                  {/* 一律以 meta 回報的供應商為準——伺服器可能因別名解析而用了別的 */}
                  {meta.provider ? (
                    <span
                      className="rounded border border-violet-500/25 bg-violet-500/10 px-2 py-0.5 font-medium text-violet-600 dark:text-violet-400"
                      title="本次實際使用的 AI 供應商與模型（來自 meta 事件）"
                    >
                      {providerLabel(providers, meta.provider)}
                      {meta.model ? <span className="ml-1 font-mono">· {meta.model}</span> : null}
                    </span>
                  ) : null}
                  {/* 回覆設定（ADR-0007）：以伺服器回報的為準，理由同供應商——
                      使用者選的可能被偏好或降級規則改掉，畫面要顯示實際生效的。 */}
                  {meta.reply?.tone_label ? (
                    <span
                      className="rounded border border-border bg-muted/50 px-1.5 py-0.5"
                      title="本次實際套用的回覆口氣"
                    >
                      {meta.reply.tone_label}
                    </span>
                  ) : null}
                  {meta.reply?.persona_name ? (
                    <span
                      className="rounded border border-border bg-muted/50 px-1.5 py-0.5"
                      title="本次套用的 Persona（風格參考，不代表本人）"
                    >
                      Persona: {meta.reply.persona_name}
                    </span>
                  ) : null}
                  {meta.reply?.custom_prompt ? (
                    <span
                      className="rounded border border-border bg-muted/50 px-1.5 py-0.5"
                      title="本次套用了自訂提示"
                    >
                      自訂提示
                    </span>
                  ) : null}
                  {/*
                    Sepia 的狀態分三種，而且必須分得出來：
                      · 綠色「Sepia」    ＝ 潤稿完成並採用
                      · 琥珀「Sepia 未套用」＝ 跑了但完整性檢查沒過（退回原文）
                      · 灰色「Sepia 潤稿中」＝ 串流結束後還在潤
                    第二種絕對不能顯示成第一種——那會讓使用者以為潤過了。
                  */}
                  {polish ? (
                    <span
                      className={cn(
                        'rounded border px-1.5 py-0.5 font-medium',
                        polish.polished
                          ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                          : 'border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-500',
                      )}
                      title={
                        polish.polished
                          ? '已用 Sepia 潤稿，事實錨點通過完整性檢查'
                          : (polish.fallback_reason ?? '潤稿未採用，顯示的是未潤稿的版本')
                      }
                    >
                      {polish.polished ? 'Sepia' : 'Sepia 未套用'}
                    </span>
                  ) : meta.reply?.sepia && !streaming && raw ? (
                    <span className="flex items-center gap-1 text-muted-foreground">
                      <Loader2Icon className="size-3 animate-spin" />
                      Sepia 潤稿中
                    </span>
                  ) : null}
                  {(meta.reference_spaces ?? []).map((ref) => (
                    <span
                      key={ref.space_id}
                      className="rounded border border-border bg-muted/50 px-1.5 py-0.5"
                    >
                      {ref.space_name} · {ref.message_count} 則
                    </span>
                  ))}
                  {streaming ? (
                    <span className="flex items-center gap-1">
                      <Loader2Icon className="size-3 animate-spin" />
                      串流中
                    </span>
                  ) : null}
                </div>
              ) : null}

              {/*
                潤稿被退回時要明說原因。只放一個琥珀 badge 不夠——
                使用者需要知道「是哪個事實被改動了」，那是判斷「模型在亂改」
                還是「檢查太嚴」的唯一依據。
              */}
              {polish && !polish.polished && polish.fallback_reason ? (
                <div className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-700 dark:text-amber-400">
                  <span className="font-medium">Sepia 潤稿未採用</span>
                  <span className="ml-1">{polish.fallback_reason}</span>
                  <span className="ml-1 text-muted-foreground">
                    下面顯示的是未潤稿的版本，內容仍然可以直接送出。
                  </span>
                </div>
              ) : null}

              {/*
                檢索結果條：在模型開口**之前**就顯示。
                這是整個功能最關鍵的 UI —— 搜錯環境、搜錯關鍵字，你一眼就看得到，
                不必先讀完一整段生成文字才發現依據是錯的。
              */}
              {(meta?.code_refs ?? []).length > 0 ? (
                <div className="space-y-1.5 rounded-lg border border-sky-500/25 bg-sky-500/5 p-2.5">
                  {(meta?.code_refs ?? []).map((ref, i) => (
                    <div key={`${ref.project_name}-${ref.environment}-${i}`} className="text-[11px]">
                      <p className="flex flex-wrap items-center gap-1.5">
                        <FileCodeIcon className="size-3.5 text-sky-600 dark:text-sky-400" aria-hidden />
                        <span className="font-medium">{ref.project_name}</span>
                        <span className="rounded bg-sky-500/15 px-1.5 py-0.5 font-medium text-sky-700 dark:text-sky-300">
                          {ref.environment_label}
                        </span>
                        <code className="font-mono text-muted-foreground">
                          {ref.branch}@{ref.commit_sha}
                        </code>
                        {ref.commit_date ? (
                          <span className="text-muted-foreground">
                            （{ref.commit_date.slice(0, 10)}）
                          </span>
                        ) : null}
                      </p>
                      {ref.terms.length > 0 ? (
                        <p className="mt-0.5 text-muted-foreground">
                          關鍵字：{ref.terms.join('、')}
                        </p>
                      ) : null}
                      <p className="mt-0.5 text-muted-foreground">
                        {ref.hit_count > 0
                          ? `命中：${ref.files.join('、')}`
                          : '這個分支沒有找到相符的程式碼'}
                        {ref.truncated ? '（已截斷）' : null}
                      </p>
                      {ref.notes.map((note, n) => (
                        <p key={n} className="mt-0.5 text-amber-600 dark:text-amber-400">
                          ※ {note}
                        </p>
                      ))}
                    </div>
                  ))}
                  {(meta?.code_skipped ?? []).length > 0 ? (
                    <p className="text-[10px] text-muted-foreground">
                      略過：{(meta?.code_skipped ?? []).join('；')}
                    </p>
                  ) : null}
                </div>
              ) : null}

              <section>
                <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold">
                  <CompassIcon className="size-4 text-sky-500" />
                  脈絡分析
                </h3>
                <Markdown
                  source={sections.context}
                  typing={streaming && !sections.replyStarted}
                  className="rounded-xl border border-border bg-card/60 p-4"
                />
              </section>

              <section>
                <div className="mb-2 flex items-center gap-1.5">
                  <MessageSquareQuoteIcon className="size-4 text-emerald-500" />
                  <h3 className="text-sm font-semibold">建議回話</h3>
                  <span className="text-[11px] text-muted-foreground">（可直接編輯）</span>
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
                  className="min-h-48 font-mono text-xs leading-relaxed"
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
                但送出後<strong className="text-foreground">
                  {meta?.answering?.length} 則
                </strong>
                會一起標成已處理——送出前請確認回話真的每一則都回到了。
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
