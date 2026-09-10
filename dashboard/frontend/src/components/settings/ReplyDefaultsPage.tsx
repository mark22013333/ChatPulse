import { useEffect, useState } from 'react'
import { BookmarkCheckIcon, Loader2Icon, SparklesIcon } from 'lucide-react'
import { toast } from 'sonner'
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
import { cn } from '@/lib/utils'
import { useDraftStore } from '@/store/draft'
import { sepiaAvailability, useReplySettingsStore } from '@/store/replySettings'

/**
 * 「我的預設值」頁（ADR-0007）：口氣、Persona、自訂提示、Sepia 潤稿。
 *
 * 與草稿工作區側欄的 `QuickReplySettings` 是同一組控件、同一份 store，
 * 差別只在這裡改完按下去會存成個人偏好，而側欄那份只影響當下這一次。
 */
export function ReplyDefaultsPage() {
  const [savingDefaults, setSavingDefaults] = useState(false)

  // 三個下拉的值與選項都住在 replyControls 裡（兩處共用），這裡只留這一頁
  // 自己要用的：自訂提示的輸入框、Sepia 開關、以及「存成預設」那顆按鈕。
  const customPrompt = useDraftStore((s) => s.customPrompt)
  const sepiaEnabled = useDraftStore((s) => s.sepiaEnabled)
  const setCustomPrompt = useDraftStore((s) => s.setCustomPrompt)
  const setSepiaEnabled = useDraftStore((s) => s.setSepiaEnabled)

  const polishers = useReplySettingsStore((s) => s.polishers)
  const load = useReplySettingsStore((s) => s.load)
  const saveDefaults = useReplySettingsStore((s) => s.saveDefaults)

  useEffect(() => {
    void load()
  }, [load])

  const sepia = sepiaAvailability(polishers)

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
        <ToneSelect id="defaults-tone" triggerClassName="max-w-md" />
      </div>

      {/* ── Persona ── */}
      <div className="flex flex-col gap-1">
        <Label htmlFor="defaults-persona" className="text-xs text-muted-foreground">
          Persona
        </Label>
        <PersonaSelect id="defaults-persona" triggerClassName="max-w-md" />
        <PersonaNotice className="max-w-md" />
      </div>

      {/* ── 自訂提示 ── */}
      <div className="flex flex-col gap-1">
        <Label htmlFor="defaults-custom-prompt" className="text-xs text-muted-foreground">
          自訂提示
        </Label>
        <PromptSelect triggerClassName="max-w-md" />
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
