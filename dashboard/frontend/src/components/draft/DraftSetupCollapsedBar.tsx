import { ChevronRightIcon, SlidersHorizontalIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useDraftStore } from '@/store/draft'
import { settingsSummary, useReplySettingsStore } from '@/store/replySettings'

/**
 * 設定側欄收合後的那一條窄帶（`DraftReplyWorkspace` 的自動收合行為）。
 *
 * 它仍然是**操作面**（raised），只是從 280px 的一欄壓成一行：
 * 1440 下主區只有約 760px，扣掉側欄之後產出卡片剩不到 440px，程式碼檔名
 * 會折行；而設定是「產生前」的事，看草稿時不需要它一直佔著。
 *
 * **收合不等於把設定藏起來。** 這一行要講得出目前套用了什麼——使用者上次
 * 選了「直接 ＋ Persona ＋ Sepia」，如果收合後只看到「草稿設定」四個字，
 * 他會以為什麼都沒選而重新設一次，或更糟：以為設定沒生效。
 * 摘要字串本身住在 store（`settingsSummary`），那裡測得到。
 */
export function DraftSetupCollapsedBar({ onExpand }: { onExpand: () => void }) {
  const referenceSpaceIds = useDraftStore((s) => s.referenceSpaceIds)
  const codeRefs = useDraftStore((s) => s.codeRefs)
  const toneId = useDraftStore((s) => s.toneId)
  const personaId = useDraftStore((s) => s.personaId)
  const customPrompt = useDraftStore((s) => s.customPrompt)
  const customPromptId = useDraftStore((s) => s.customPromptId)
  const sepiaEnabled = useDraftStore((s) => s.sepiaEnabled)

  const tones = useReplySettingsStore((s) => s.tones)
  const personas = useReplySettingsStore((s) => s.personas)

  const summary = settingsSummary({
    tones,
    toneId,
    personas,
    personaId,
    customPrompt,
    customPromptId,
    sepiaEnabled,
  })

  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-border bg-raised px-5 py-2">
      <span className="flex shrink-0 items-center gap-1.5 text-xs font-semibold">
        <SlidersHorizontalIcon className="size-3.5" aria-hidden />
        草稿設定
      </span>
      {/* 分隔用細豎線不用中點（設計原則禁用 `·`） */}
      <span aria-hidden className="h-3 w-px bg-line-strong" />
      <span className="shrink-0 text-2xs text-muted-foreground">
        參考 Space {referenceSpaceIds.length} 個
      </span>
      <span aria-hidden className="h-3 w-px bg-line-strong" />
      <span className="shrink-0 text-2xs text-muted-foreground">
        參考專案 {codeRefs.length} 個
      </span>
      <span aria-hidden className="h-3 w-px bg-line-strong" />
      <span className="min-w-0 truncate text-2xs text-muted-foreground">{summary}</span>

      {/* 展開鈕帶 aria-expanded ＋ aria-controls，說明它控制的是哪一塊。
          按鈕自己就有可見文字，不掛 tooltip（§17 紅線第 8 條）。 */}
      <Button
        size="xs"
        variant="ghost"
        className="ml-auto shrink-0"
        onClick={onExpand}
        aria-expanded={false}
        aria-controls="draft-setup-panel"
      >
        <ChevronRightIcon />
        展開設定
      </Button>
    </div>
  )
}
