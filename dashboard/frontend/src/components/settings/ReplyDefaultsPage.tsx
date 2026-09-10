import { useEffect, useState } from 'react'
import { BookmarkCheckIcon, Loader2Icon, SparklesIcon } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { NONE_ID, useDraftStore } from '@/store/draft'
import {
  INHERIT,
  NONE,
  PUBLIC_FIGURE_NOTICE,
  needsPublicFigureNotice,
  personaSourceLine,
  selectToId,
  selectToTone,
  sepiaAvailability,
  toneLabel,
  useReplySettingsStore,
} from '@/store/replySettings'

/**
 * 「我的預設值」頁（ADR-0007）：口氣、Persona、自訂提示、Sepia 潤稿。
 *
 * 與草稿工作區側欄的 `QuickReplySettings` 是同一組控件、同一份 store，
 * 差別只在這裡改完按下去會存成個人偏好，而側欄那份只影響當下這一次。
 */
export function ReplyDefaultsPage() {
  const [savingDefaults, setSavingDefaults] = useState(false)

  const toneId = useDraftStore((s) => s.toneId)
  const personaId = useDraftStore((s) => s.personaId)
  const customPrompt = useDraftStore((s) => s.customPrompt)
  const customPromptId = useDraftStore((s) => s.customPromptId)
  const sepiaEnabled = useDraftStore((s) => s.sepiaEnabled)
  const setToneId = useDraftStore((s) => s.setToneId)
  const setPersonaId = useDraftStore((s) => s.setPersonaId)
  const setCustomPrompt = useDraftStore((s) => s.setCustomPrompt)
  const setCustomPromptId = useDraftStore((s) => s.setCustomPromptId)
  const setSepiaEnabled = useDraftStore((s) => s.setSepiaEnabled)

  const tones = useReplySettingsStore((s) => s.tones)
  const serverDefaultTone = useReplySettingsStore((s) => s.serverDefaultTone)
  const personas = useReplySettingsStore((s) => s.personas)
  const replyPrompts = useReplySettingsStore((s) => s.replyPrompts)
  const polishers = useReplySettingsStore((s) => s.polishers)
  const load = useReplySettingsStore((s) => s.load)
  const saveDefaults = useReplySettingsStore((s) => s.saveDefaults)

  useEffect(() => {
    void load()
  }, [load])

  const sepia = sepiaAvailability(polishers)
  const enabledPersonas = personas.filter((p) => p.enabled)
  const activePersona = personas.find((p) => p.id === personaId)

  const toneValue = toneId ?? INHERIT
  const toneItems: Record<string, string> = {
    [INHERIT]: `跟隨預設（${toneLabel(tones, serverDefaultTone) || serverDefaultTone}）`,
    ...Object.fromEntries(tones.map((t) => [t.id, t.label])),
  }

  const personaValue = personaId === null ? INHERIT : personaId === NONE_ID ? NONE : String(personaId)
  const personaItems: Record<string, string> = {
    [INHERIT]: '跟隨預設',
    [NONE]: '不使用 Persona',
    ...Object.fromEntries(enabledPersonas.map((p) => [String(p.id), p.name])),
  }

  const promptValue =
    customPromptId === null ? INHERIT : customPromptId === NONE_ID ? NONE : String(customPromptId)
  const promptItems: Record<string, string> = {
    [INHERIT]: '跟隨預設',
    [NONE]: '不套用',
    ...Object.fromEntries(replyPrompts.map((p) => [String(p.id), p.name])),
  }

  const handleSaveDefaults = async () => {
    setSavingDefaults(true)
    const ok = await saveDefaults()
    setSavingDefaults(false)
    if (ok) toast.success('已把目前的回覆設定存成預設')
    else toast.error(useReplySettingsStore.getState().error ?? '儲存偏好失敗')
  }

  return (
    <div className="space-y-6">
      <h2 className="text-sm font-semibold">我的預設值</h2>

      {/* ── 口氣 ── */}
      <div className="flex flex-col gap-1">
        <Label htmlFor="defaults-tone" className="text-xs text-muted-foreground">
          口氣
        </Label>
        <Select
          items={toneItems}
          value={toneValue}
          onValueChange={(v) => {
            const next = selectToTone(v as string | null)
            if (next !== undefined) setToneId(next)
          }}
        >
          <SelectTrigger id="defaults-tone" size="sm" className="w-full max-w-md">
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="w-auto max-w-96 min-w-72">
            <SelectItem value={INHERIT}>
              <span className="flex w-full flex-col gap-0.5 whitespace-normal">
                <span className="font-medium">{toneItems[INHERIT]}</span>
                <span className="text-2xs leading-snug text-muted-foreground">
                  不指定口氣，沿用你的偏好或系統預設。
                </span>
              </span>
            </SelectItem>
            {/* SelectItem 不放 title：description 在下一行已經可見，是真重複 */}
            {tones.map((tone) => (
              <SelectItem key={tone.id} value={tone.id}>
                <span className="flex w-full flex-col gap-0.5 whitespace-normal">
                  <span className="font-medium">{tone.label}</span>
                  <span className="text-2xs leading-snug text-muted-foreground">
                    {tone.description}
                  </span>
                  {/* 固定範例：所有 tone 的範例都在講同一個事實，
                      並排看得出「變的是語氣、不是內容」，也不必為了預覽打一次 AI */}
                  <span className="text-2xs leading-snug text-signal">
                    例：{tone.example}
                  </span>
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* ── Persona ── */}
      <div className="flex flex-col gap-1">
        <Label htmlFor="defaults-persona" className="text-xs text-muted-foreground">
          Persona
        </Label>
        <Select
          items={personaItems}
          value={personaValue}
          onValueChange={(v) => {
            const next = selectToId(v as string | null, NONE_ID)
            if (next !== undefined) setPersonaId(next)
          }}
        >
          <SelectTrigger id="defaults-persona" size="sm" className="w-full max-w-md">
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="w-auto max-w-96 min-w-72">
            <SelectItem value={INHERIT}>
              <span className="text-xs">跟隨預設</span>
            </SelectItem>
            <SelectItem value={NONE}>
              <span className="text-xs">不使用 Persona</span>
            </SelectItem>
            {enabledPersonas.map((persona) => (
              <SelectItem key={persona.id} value={String(persona.id)}>
                <span className="flex w-full flex-col gap-0.5 whitespace-normal">
                  <span className="font-medium">{persona.name}</span>
                  {persona.description ? (
                    <span className="text-2xs leading-snug text-muted-foreground">
                      {persona.description}
                    </span>
                  ) : null}
                  <span className="metric text-2xs text-muted-foreground">
                    {personaSourceLine(persona)}
                  </span>
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {activePersona ? (
          <div className="max-w-md space-y-0.5 rounded border border-border bg-muted/40 px-2 py-1.5">
            <p className="metric text-2xs text-muted-foreground">
              {personaSourceLine(activePersona)}
            </p>
            {activePersona.refreshed_at ? (
              <p className="text-2xs text-muted-foreground">
                最後更新 {activePersona.refreshed_at.slice(0, 10)}
              </p>
            ) : (
              <p className="text-2xs text-muted-foreground">
                匯入於 {activePersona.imported_at.slice(0, 10)}
              </p>
            )}
            {needsPublicFigureNotice(activePersona) ? (
              <p className="text-2xs leading-snug text-caution">
                {PUBLIC_FIGURE_NOTICE}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>

      {/* ── 自訂提示 ── */}
      <div className="flex flex-col gap-1">
        <Label htmlFor="defaults-custom-prompt" className="text-xs text-muted-foreground">
          自訂提示
        </Label>
        {replyPrompts.length ? (
          <Select
            items={promptItems}
            value={promptValue}
            onValueChange={(v) => {
              const next = selectToId(v as string | null, NONE_ID)
              if (next === undefined) return
              setCustomPromptId(next)
              // 套用 preset 就把內容填進輸入框，讓使用者看得到、也改得動。
              // 送出時 inline 內容優先於 preset id（後端規則），所以填進去
              // 之後實際送的是這段文字——這正是「套用後可微調」該有的行為。
              const preset = next ? replyPrompts.find((p) => p.id === next) : undefined
              if (preset) setCustomPrompt(preset.prompt)
            }}
          >
            <SelectTrigger size="sm" className="w-full max-w-md">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="w-auto max-w-96 min-w-72">
              <SelectItem value={INHERIT}>
                <span className="text-xs">跟隨預設</span>
              </SelectItem>
              <SelectItem value={NONE}>
                <span className="text-xs">不套用</span>
              </SelectItem>
              {replyPrompts.map((preset) => (
                <SelectItem key={preset.id} value={String(preset.id)}>
                  <span className="flex w-full flex-col gap-0.5 whitespace-normal">
                    <span className="font-medium">{preset.name}</span>
                    {preset.description ? (
                      <span className="text-2xs leading-snug text-muted-foreground">
                        {preset.description}
                      </span>
                    ) : null}
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : null}
        <Textarea
          id="defaults-custom-prompt"
          value={customPrompt}
          onChange={(e) => setCustomPrompt(e.target.value)}
          rows={3}
          maxLength={2000}
          className="min-h-16 max-w-md text-xs"
          placeholder="例如：不要太客套，直接說目前卡在哪裡；如果需要對方補資料，就列出需要的資訊。"
        />
      </div>

      {/* ── Sepia 潤稿 ── */}
      <div className="space-y-1">
        <label
          className={cn(
            'flex max-w-md items-start gap-2',
            sepia.available ? 'cursor-pointer' : 'cursor-not-allowed opacity-60',
          )}
        >
          <Checkbox
            checked={sepiaEnabled === true}
            disabled={!sepia.available}
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

      <div className="space-y-2">
        <p className="max-w-md text-xs leading-relaxed text-muted-foreground">
          這裡設定的是<strong className="font-medium">以後每次</strong>
          的預設值。只想改這一次的話，在草稿工作區的回覆設定改就好。
        </p>
        {/* 這是本頁唯一的主要動作，用 default variant。原本它在側欄裡是一顆
            ghost 小按鈕（那裡它只是順手功能），搬到這裡就該長得像主要動作。 */}
        <Button type="button" disabled={savingDefaults} onClick={() => void handleSaveDefaults()}>
          {savingDefaults ? (
            <Loader2Icon className="animate-spin" />
          ) : (
            <BookmarkCheckIcon />
          )}
          儲存為我的預設
        </Button>
      </div>
    </div>
  )
}
