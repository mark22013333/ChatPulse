import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
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
  toneLabel,
  useReplySettingsStore,
} from '@/store/replySettings'

/**
 * 回覆設定的三個下拉，給兩個地方共用（ADR-0007）。
 *
 * 「這一次」的設定（`QuickReplySettings`，草稿工作區側欄）與「以後每次」的
 * 預設值（`ReplyDefaultsPage`，設定中心）**讀寫的是同一份 store**——差別只在
 * 後者按下「存成預設」才會寫進個人偏好。所以兩邊的選項內容、值的推導、
 * 選到之後要做什麼，全部都該是同一份程式碼。
 *
 * 抽出來之前這三組 `SelectContent` 在兩個檔案裡逐字重複，改一邊忘了另一邊
 * 就會讓「這一次」與「以後每次」長得不一樣——而那正是使用者最沒有理由
 * 預期的不一致。
 *
 * 兩邊唯一真正的差異在 trigger：草稿側欄要 `disabled`（串流中不讓改）、
 * 設定頁要 `max-w-md`（整頁寬度下不該拉成一條）。所以只有這幾個進 props。
 */
interface ReplyControlProps {
  /** 給 `<Label htmlFor>` 用。沒有關聯標籤的（自訂提示）可以不給。 */
  id?: string
  /** trigger 的寬度：側欄 `w-full`、設定頁 `w-full max-w-md`。 */
  triggerClassName?: string
  /** 串流中不讓改。 */
  disabled?: boolean
}

export function ToneSelect({ id, triggerClassName, disabled }: ReplyControlProps) {
  const toneId = useDraftStore((s) => s.toneId)
  const setToneId = useDraftStore((s) => s.setToneId)
  const tones = useReplySettingsStore((s) => s.tones)
  const serverDefaultTone = useReplySettingsStore((s) => s.serverDefaultTone)

  const items: Record<string, string> = {
    [INHERIT]: `跟隨預設（${toneLabel(tones, serverDefaultTone) || serverDefaultTone}）`,
    ...Object.fromEntries(tones.map((t) => [t.id, t.label])),
  }

  return (
    <Select
      items={items}
      value={toneId ?? INHERIT}
      onValueChange={(v) => {
        const next = selectToTone(v as string | null)
        if (next !== undefined) setToneId(next)
      }}
    >
      <SelectTrigger id={id} size="sm" className={cn('w-full', triggerClassName)} disabled={disabled}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent className="w-auto max-w-96 min-w-72">
        <SelectItem value={INHERIT}>
          <span className="flex w-full flex-col gap-0.5 whitespace-normal">
            <span className="font-medium">{items[INHERIT]}</span>
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
              <span className="text-2xs leading-snug text-signal">例：{tone.example}</span>
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

export function PersonaSelect({ id, triggerClassName, disabled }: ReplyControlProps) {
  const personaId = useDraftStore((s) => s.personaId)
  const setPersonaId = useDraftStore((s) => s.setPersonaId)
  const personas = useReplySettingsStore((s) => s.personas)

  const enabled = personas.filter((p) => p.enabled)
  const value = personaId === null ? INHERIT : personaId === NONE_ID ? NONE : String(personaId)
  const items: Record<string, string> = {
    [INHERIT]: '跟隨預設',
    [NONE]: '不使用 Persona',
    ...Object.fromEntries(enabled.map((p) => [String(p.id), p.name])),
  }

  return (
    <Select
      items={items}
      value={value}
      onValueChange={(v) => {
        const next = selectToId(v as string | null, NONE_ID)
        if (next !== undefined) setPersonaId(next)
      }}
    >
      <SelectTrigger id={id} size="sm" className={cn('w-full', triggerClassName)} disabled={disabled}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent className="w-auto max-w-96 min-w-72">
        <SelectItem value={INHERIT}>
          <span className="text-xs">跟隨預設</span>
        </SelectItem>
        <SelectItem value={NONE}>
          <span className="text-xs">不使用 Persona</span>
        </SelectItem>
        {enabled.map((persona) => (
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
  )
}

/**
 * 選了 Persona 之後那張來源說明卡。沒選就不畫。
 *
 * `PUBLIC_FIGURE_NOTICE` 是這裡唯一不能省的東西：模仿在世公眾人物的語氣
 * 有其後果，而使用者按下送出之前只有這一個地方會提醒他。
 */
export function PersonaNotice({ className }: { className?: string }) {
  const personaId = useDraftStore((s) => s.personaId)
  const personas = useReplySettingsStore((s) => s.personas)
  const persona = personas.find((p) => p.id === personaId)
  if (!persona) return null

  return (
    <div className={cn('space-y-0.5 rounded border border-border bg-muted/40 px-2 py-1.5', className)}>
      <p className="metric text-2xs text-muted-foreground">{personaSourceLine(persona)}</p>
      {persona.refreshed_at ? (
        <p className="text-2xs text-muted-foreground">
          最後更新 {persona.refreshed_at.slice(0, 10)}
        </p>
      ) : (
        <p className="text-2xs text-muted-foreground">
          匯入於 {persona.imported_at.slice(0, 10)}
        </p>
      )}
      {needsPublicFigureNotice(persona) ? (
        <p className="text-2xs leading-snug text-caution">{PUBLIC_FIGURE_NOTICE}</p>
      ) : null}
    </div>
  )
}

/** 一則常用提示詞都沒有時不畫這個下拉——空的選單只會讓人以為壞了。 */
export function PromptSelect({ id, triggerClassName, disabled }: ReplyControlProps) {
  const customPromptId = useDraftStore((s) => s.customPromptId)
  const setCustomPromptId = useDraftStore((s) => s.setCustomPromptId)
  const setCustomPrompt = useDraftStore((s) => s.setCustomPrompt)
  const replyPrompts = useReplySettingsStore((s) => s.replyPrompts)

  if (replyPrompts.length === 0) return null

  const value =
    customPromptId === null ? INHERIT : customPromptId === NONE_ID ? NONE : String(customPromptId)
  const items: Record<string, string> = {
    [INHERIT]: '跟隨預設',
    [NONE]: '不套用',
    ...Object.fromEntries(replyPrompts.map((p) => [String(p.id), p.name])),
  }

  return (
    <Select
      items={items}
      value={value}
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
      <SelectTrigger id={id} size="sm" className={cn('w-full', triggerClassName)} disabled={disabled}>
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
  )
}
