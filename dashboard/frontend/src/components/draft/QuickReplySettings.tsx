import { useEffect, useState } from 'react'
import {
  ChevronDownIcon,
  ChevronRightIcon,
  SettingsIcon,
  SlidersHorizontalIcon,
  SparklesIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import {
  PersonaNotice,
  PersonaSelect,
  PromptSelect,
  ToneSelect,
} from '@/components/settings/replyControls'
import { Textarea } from '@/components/ui/textarea'
import { hashForSettings } from '@/lib/route'
import { cn } from '@/lib/utils'
import { useRouter } from '@/router/useRouter'
import { useDraftStore } from '@/store/draft'
import {
  sepiaAvailability,
  settingsSummary,
  useReplySettingsStore,
} from '@/store/replySettings'

interface QuickReplySettingsProps {
  /** 串流中就不讓改 */
  disabled?: boolean
}

/**
 * Draft Reply 的回覆設定（ADR-0007）：口氣、Persona、自訂提示、Sepia 潤稿。
 *
 * 這裡改的是**這一次**的設定。要改「以後每次」的預設值，走設定中心的
 * 「我的預設值」頁（`ReplyDefaultsPage`）。
 */
export function QuickReplySettings({ disabled }: QuickReplySettingsProps) {
  const { navigate } = useRouter()

  // **預設展開。** 這裡原本是預設收合（理由是側欄已經有四組控件），
  // 但實測的結果是使用者根本找不到它——在 280px 寬的側欄裡，一行
  // text-xs 標題加一行灰色小字基本上是隱形的，而找不到的功能等於沒做。
  //
  // 展開是安全的：側欄的 SpaceList 是 flex-1 overflow-y-auto，會吸收
  // 剩餘空間並自己捲動，所以這一區變高只會讓 Space 清單矮一點，
  // 不會把版面推爆。
  const [open, setOpen] = useState(true)

  const toneId = useDraftStore((s) => s.toneId)
  const personaId = useDraftStore((s) => s.personaId)
  const customPrompt = useDraftStore((s) => s.customPrompt)
  const customPromptId = useDraftStore((s) => s.customPromptId)
  const sepiaEnabled = useDraftStore((s) => s.sepiaEnabled)
  const setCustomPrompt = useDraftStore((s) => s.setCustomPrompt)
  const setSepiaEnabled = useDraftStore((s) => s.setSepiaEnabled)

  // 三個下拉的值與選項住在 replyControls 裡（與設定中心那頁共用）。這裡還
  // 需要 toneId／personaId 等原始值，是為了收合時那一行摘要。
  const tones = useReplySettingsStore((s) => s.tones)
  const personas = useReplySettingsStore((s) => s.personas)
  const polishers = useReplySettingsStore((s) => s.polishers)
  const load = useReplySettingsStore((s) => s.load)

  useEffect(() => {
    void load()
  }, [load])

  const sepia = sepiaAvailability(polishers)
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
    <div className="shrink-0 space-y-2 border-t border-border px-3 py-2.5">
      <button
        type="button"
        className="flex w-full items-center gap-1.5 text-left"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {open ? (
          <ChevronDownIcon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        ) : (
          <ChevronRightIcon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        )}
        <SlidersHorizontalIcon className="size-3.5 shrink-0" aria-hidden />
        <span className="text-xs font-semibold">回覆設定</span>
        {/* 收合時才顯示摘要——展開時每個選項本來就看得到，再顯示一次是重複 */}
        {!open ? (
          <span className="ml-auto truncate text-2xs text-muted-foreground">
            {summary}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="space-y-2.5 pt-1">
          {/* ── 口氣 ── */}
          <div className="flex flex-col gap-1">
            <Label htmlFor="draft-tone" className="text-xs text-muted-foreground">
              口氣
            </Label>
            <ToneSelect id="draft-tone" disabled={disabled} />
          </div>

          {/* ── Persona ── */}
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-1">
              <Label htmlFor="draft-persona" className="text-xs text-muted-foreground">
                Persona
              </Label>
              <Button
                type="button"
                size="xs"
                variant="ghost"
                className="ml-auto h-4 px-1 text-2xs text-muted-foreground"
                onClick={() => navigate(hashForSettings('personas'))}
              >
                <SettingsIcon className="size-3" />
                管理
              </Button>
            </div>
            <PersonaSelect id="draft-persona" disabled={disabled} />
            <PersonaNotice />
          </div>

          {/* ── 自訂提示 ── */}
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-1">
              <Label htmlFor="draft-custom-prompt" className="text-xs text-muted-foreground">
                自訂提示
              </Label>
              <Button
                type="button"
                size="xs"
                variant="ghost"
                className="ml-auto h-4 px-1 text-2xs text-muted-foreground"
                onClick={() => navigate(hashForSettings('prompts'))}
              >
                <SettingsIcon className="size-3" />
                管理
              </Button>
            </div>
            <PromptSelect disabled={disabled} />
            <Textarea
              id="draft-custom-prompt"
              value={customPrompt}
              onChange={(e) => setCustomPrompt(e.target.value)}
              disabled={disabled}
              rows={3}
              maxLength={2000}
              className="min-h-16 text-xs"
              placeholder="例如：不要太客套，直接說目前卡在哪裡；如果需要對方補資料，就列出需要的資訊。"
            />
          </div>

          {/* ── Sepia 潤稿 ── */}
          <div className="space-y-1">
            <label
              className={cn(
                'flex items-start gap-2',
                sepia.available && !disabled ? 'cursor-pointer' : 'cursor-not-allowed opacity-60',
              )}
            >
              <Checkbox
                checked={sepiaEnabled === true}
                disabled={disabled || !sepia.available}
                onCheckedChange={(checked) => setSepiaEnabled(checked === true)}
                className="mt-px"
              />
              <span className="flex flex-col gap-0.5">
                <span className="flex items-center gap-1 text-xs font-medium">
                  <SparklesIcon className="size-3 text-provenance" aria-hidden />
                  使用 Sepia 潤稿
                </span>
                <span className="text-2xs leading-snug text-muted-foreground">
                  只調整〈建議回話〉的自然度與節奏，不會改動事實、數字或程式碼佐證。
                </span>
              </span>
            </label>
            {!sepia.available && sepia.reason ? (
              <p className="text-2xs leading-snug text-caution">
                {sepia.reason}
              </p>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  )
}
