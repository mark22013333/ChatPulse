import { useEffect, useState } from 'react'
import {
  BookmarkCheckIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  DownloadIcon,
  Loader2Icon,
  RefreshCwIcon,
  SettingsIcon,
  SlidersHorizontalIcon,
  SparklesIcon,
  Trash2Icon,
} from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
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
  settingsSummary,
  toneLabel,
  useReplySettingsStore,
} from '@/store/replySettings'
import type { Persona } from '@/lib/types'

interface ReplySettingsProps {
  /** 串流中就不讓改 */
  disabled?: boolean
}

/**
 * Draft Reply 的回覆設定（ADR-0007）：口氣、Persona、自訂提示、Sepia 潤稿。
 *
 * 預設收合，因為側欄已經有 Reference Space、抓取則數、供應商與參考專案
 * 四組控件了。收合時標題列顯示當前設定摘要（見 `settingsSummary`）。
 */
export function ReplySettings({ disabled }: ReplySettingsProps) {
  // **預設展開。** 這裡原本是預設收合（理由是側欄已經有四組控件），
  // 但實測的結果是使用者根本找不到它——在 280px 寬的側欄裡，一行
  // text-xs 標題加一行灰色小字基本上是隱形的，而找不到的功能等於沒做。
  //
  // 展開是安全的：側欄的 SpaceList 是 flex-1 overflow-y-auto，會吸收
  // 剩餘空間並自己捲動，所以這一區變高只會讓 Space 清單矮一點，
  // 不會把版面推爆。
  const [open, setOpen] = useState(true)
  const [personaDialog, setPersonaDialog] = useState(false)
  const [promptDialog, setPromptDialog] = useState(false)
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
  const sepiaRules = useReplySettingsStore((s) => s.sepiaRules)
  const load = useReplySettingsStore((s) => s.load)
  const saveDefaults = useReplySettingsStore((s) => s.saveDefaults)

  useEffect(() => {
    void load()
  }, [load])

  const sepia = sepiaAvailability(polishers)
  const enabledPersonas = personas.filter((p) => p.enabled)
  const activePersona = personas.find((p) => p.id === personaId)
  const summary = settingsSummary({
    tones,
    toneId,
    personas,
    personaId,
    customPrompt,
    customPromptId,
    sepiaEnabled,
  })

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
          <span className="ml-auto truncate text-[10px] text-muted-foreground" title={summary}>
            {summary}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="space-y-2.5 pt-1">
          {/* ── 口氣 ── */}
          <div className="flex flex-col gap-1">
            <Label htmlFor="draft-tone" className="text-[11px] text-muted-foreground">
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
              <SelectTrigger id="draft-tone" size="sm" className="w-full" disabled={disabled}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="w-auto max-w-96 min-w-72">
                <SelectItem value={INHERIT}>
                  <span className="flex w-full flex-col gap-0.5 whitespace-normal">
                    <span className="font-medium">{toneItems[INHERIT]}</span>
                    <span className="text-[10px] leading-snug text-muted-foreground">
                      不指定口氣，沿用你的偏好或系統預設。
                    </span>
                  </span>
                </SelectItem>
                {tones.map((tone) => (
                  <SelectItem key={tone.id} value={tone.id} title={tone.description}>
                    <span className="flex w-full flex-col gap-0.5 whitespace-normal">
                      <span className="font-medium">{tone.label}</span>
                      <span className="text-[10px] leading-snug text-muted-foreground">
                        {tone.description}
                      </span>
                      {/* 固定範例：所有 tone 的範例都在講同一個事實，
                          並排看得出「變的是語氣、不是內容」，也不必為了預覽打一次 AI */}
                      <span className="text-[10px] leading-snug text-sky-600 dark:text-sky-400">
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
            <div className="flex items-center gap-1">
              <Label htmlFor="draft-persona" className="text-[11px] text-muted-foreground">
                Persona
              </Label>
              <Button
                type="button"
                size="xs"
                variant="ghost"
                className="ml-auto h-4 px-1 text-[10px] text-muted-foreground"
                onClick={() => setPersonaDialog(true)}
                title="匯入與管理 Persona"
              >
                <SettingsIcon className="size-3" />
                管理
              </Button>
            </div>
            <Select
              items={personaItems}
              value={personaValue}
              onValueChange={(v) => {
                const next = selectToId(v as string | null, NONE_ID)
                if (next !== undefined) setPersonaId(next)
              }}
            >
              <SelectTrigger id="draft-persona" size="sm" className="w-full" disabled={disabled}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="w-auto max-w-96 min-w-72">
                <SelectItem value={INHERIT}>
                  <span className="text-[11px]">跟隨預設</span>
                </SelectItem>
                <SelectItem value={NONE}>
                  <span className="text-[11px]">不使用 Persona</span>
                </SelectItem>
                {enabledPersonas.map((persona) => (
                  <SelectItem key={persona.id} value={String(persona.id)}>
                    <span className="flex w-full flex-col gap-0.5 whitespace-normal">
                      <span className="font-medium">{persona.name}</span>
                      {persona.description ? (
                        <span className="text-[10px] leading-snug text-muted-foreground">
                          {persona.description}
                        </span>
                      ) : null}
                      <span className="font-mono text-[10px] text-muted-foreground">
                        {personaSourceLine(persona)}
                      </span>
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {activePersona ? (
              <div className="space-y-0.5 rounded border border-border bg-muted/40 px-2 py-1.5">
                <p className="font-mono text-[10px] text-muted-foreground">
                  {personaSourceLine(activePersona)}
                </p>
                {activePersona.refreshed_at ? (
                  <p className="text-[10px] text-muted-foreground">
                    最後更新 {activePersona.refreshed_at.slice(0, 10)}
                  </p>
                ) : (
                  <p className="text-[10px] text-muted-foreground">
                    匯入於 {activePersona.imported_at.slice(0, 10)}
                  </p>
                )}
                {needsPublicFigureNotice(activePersona) ? (
                  <p className="text-[10px] leading-snug text-amber-600 dark:text-amber-500">
                    {PUBLIC_FIGURE_NOTICE}
                  </p>
                ) : null}
              </div>
            ) : null}
          </div>

          {/* ── 自訂提示 ── */}
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-1">
              <Label htmlFor="draft-custom-prompt" className="text-[11px] text-muted-foreground">
                自訂提示
              </Label>
              <Button
                type="button"
                size="xs"
                variant="ghost"
                className="ml-auto h-4 px-1 text-[10px] text-muted-foreground"
                onClick={() => setPromptDialog(true)}
                title="管理常用提示詞"
              >
                <SettingsIcon className="size-3" />
                管理
              </Button>
            </div>
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
                <SelectTrigger size="sm" className="w-full" disabled={disabled}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="w-auto max-w-96 min-w-72">
                  <SelectItem value={INHERIT}>
                    <span className="text-[11px]">跟隨預設</span>
                  </SelectItem>
                  <SelectItem value={NONE}>
                    <span className="text-[11px]">不套用</span>
                  </SelectItem>
                  {replyPrompts.map((preset) => (
                    <SelectItem key={preset.id} value={String(preset.id)}>
                      <span className="flex w-full flex-col gap-0.5 whitespace-normal">
                        <span className="font-medium">{preset.name}</span>
                        {preset.description ? (
                          <span className="text-[10px] leading-snug text-muted-foreground">
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
              title={
                sepia.available
                  ? '在保留原始事實與技術內容的前提下，調整回覆的自然度、節奏與 AI 味。'
                  : sepia.reason
              }
            >
              <Checkbox
                checked={sepiaEnabled === true}
                disabled={disabled || !sepia.available}
                onCheckedChange={(checked) => setSepiaEnabled(checked === true)}
                className="mt-px"
              />
              <span className="flex flex-col gap-0.5">
                <span className="flex items-center gap-1 text-[11px] font-medium">
                  <SparklesIcon className="size-3 text-violet-500" aria-hidden />
                  使用 Sepia 潤稿
                </span>
                <span className="text-[10px] leading-snug text-muted-foreground">
                  只調整〈建議回話〉的自然度與節奏，不會改動事實、數字或程式碼佐證。
                </span>
              </span>
            </label>
            {!sepia.available && sepia.reason ? (
              <p className="text-[10px] leading-snug text-amber-600 dark:text-amber-500">
                {sepia.reason}
              </p>
            ) : null}
            {sepia.available && sepiaRules.version ? (
              <p className="font-mono text-[10px] text-muted-foreground">
                sepia v{sepiaRules.version}
                {sepiaRules.source_commit_sha
                  ? ` @ ${sepiaRules.source_commit_sha.slice(0, 7)}`
                  : ''}
              </p>
            ) : null}
          </div>

          <Button
            type="button"
            size="xs"
            variant="ghost"
            className="h-5 w-full text-[10px] text-muted-foreground"
            disabled={savingDefaults}
            onClick={() => void handleSaveDefaults()}
            title="把目前的口氣／Persona／提示詞／潤稿設定存成個人預設"
          >
            {savingDefaults ? (
              <Loader2Icon className="size-3 animate-spin" />
            ) : (
              <BookmarkCheckIcon className="size-3" />
            )}
            設為預設
          </Button>
        </div>
      ) : null}

      <PersonaManagerDialog open={personaDialog} onOpenChange={setPersonaDialog} />
      <ReplyPromptManagerDialog open={promptDialog} onOpenChange={setPromptDialog} />
    </div>
  )
}

// ==========================================================================
// Persona 管理
// ==========================================================================

function PersonaManagerDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const personas = useReplySettingsStore((s) => s.personas)
  const busy = useReplySettingsStore((s) => s.busy)
  const importPersona = useReplySettingsStore((s) => s.importPersona)
  const refreshPersona = useReplySettingsStore((s) => s.refreshPersona)
  const deletePersona = useReplySettingsStore((s) => s.deletePersona)
  const updatePersona = useReplySettingsStore((s) => s.updatePersona)

  const [mode, setMode] = useState<'github' | 'url'>('github')
  const [repository, setRepository] = useState('')
  const [slug, setSlug] = useState('')
  const [url, setUrl] = useState('')
  const [name, setName] = useState('')

  const handleImport = async () => {
    const body =
      mode === 'github'
        ? { source_type: 'github', repository: repository.trim(), persona: slug.trim() }
        : { source_type: 'url', url: url.trim() }
    const persona = await importPersona({ ...body, name: name.trim() || undefined })
    if (persona) {
      toast.success(`已匯入 Persona「${persona.name}」`)
      setRepository('')
      setSlug('')
      setUrl('')
      setName('')
    } else {
      toast.error(useReplySettingsStore.getState().error ?? '匯入失敗')
    }
  }

  const canImport =
    !busy && (mode === 'github' ? repository.trim() && slug.trim() : url.trim().length > 0)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Persona</DialogTitle>
          <DialogDescription>
            Persona 是「借用某個人的思考框架與表達習慣來寫回覆」，不是扮演那個人。
            匯入時會固定版本（記下 commit），遠端之後改了也不會影響已產生的回話。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2 rounded border border-border p-3">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold">匯入</span>
              <div className="ml-auto flex gap-1">
                {(['github', 'url'] as const).map((m) => (
                  <Button
                    key={m}
                    type="button"
                    size="xs"
                    variant={mode === m ? 'default' : 'ghost'}
                    className="h-5 px-2 text-[10px]"
                    onClick={() => setMode(m)}
                  >
                    {m === 'github' ? 'Repository' : '網址'}
                  </Button>
                ))}
              </div>
            </div>

            {mode === 'github' ? (
              <div className="grid gap-2 sm:grid-cols-2">
                <div className="flex flex-col gap-1">
                  <Label htmlFor="persona-repo" className="text-[11px] text-muted-foreground">
                    Repository（owner/repo）
                  </Label>
                  <Input
                    id="persona-repo"
                    value={repository}
                    onChange={(e) => setRepository(e.target.value)}
                    placeholder="fxp/persona-distill-skills"
                    className="h-7 font-mono text-xs"
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <Label htmlFor="persona-slug" className="text-[11px] text-muted-foreground">
                    Persona 名稱（目錄名）
                  </Label>
                  <Input
                    id="persona-slug"
                    value={slug}
                    onChange={(e) => setSlug(e.target.value)}
                    placeholder="luozhenyu"
                    className="h-7 font-mono text-xs"
                  />
                </div>
              </div>
            ) : (
              <div className="flex flex-col gap-1">
                <Label htmlFor="persona-url" className="text-[11px] text-muted-foreground">
                  檔案網址（只接受 github.com 與 raw.githubusercontent.com）
                </Label>
                <Input
                  id="persona-url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://raw.githubusercontent.com/owner/repo/main/personas/x/SKILL.md"
                  className="h-7 font-mono text-xs"
                />
              </div>
            )}

            <div className="flex flex-col gap-1">
              <Label htmlFor="persona-name" className="text-[11px] text-muted-foreground">
                顯示名稱（選填，來源檔案的名稱常常是識別字而不是人名）
              </Label>
              <Input
                id="persona-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="留空則用來源檔案裡的名稱"
                className="h-7 text-xs"
              />
            </div>

            <Button
              type="button"
              size="sm"
              className="w-full"
              disabled={!canImport}
              onClick={() => void handleImport()}
            >
              {busy ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <DownloadIcon className="size-4" />
              )}
              匯入
            </Button>
            <p className="text-[10px] leading-relaxed text-muted-foreground">
              匯入的內容會經過淨化：角色扮演指令、工具呼叫、讀檔要求、以及「不知道就推測」
              這類授權一律不採用。只有表達與思考風格會被保留。
            </p>
          </div>

          <div className="space-y-2">
            <span className="text-xs font-semibold">已匯入（{personas.length}）</span>
            {personas.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">還沒有匯入任何 Persona。</p>
            ) : (
              <div className="max-h-64 space-y-2 overflow-y-auto">
                {personas.map((persona) => (
                  <div key={persona.id} className="space-y-1 rounded border border-border p-2">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium">{persona.name}</span>
                      {!persona.enabled ? (
                        <span className="rounded border border-border px-1 py-px text-[10px] text-muted-foreground">
                          已停用
                        </span>
                      ) : null}
                      <div className="ml-auto flex gap-1">
                        <Button
                          type="button"
                          size="xs"
                          variant="ghost"
                          className="h-5 px-1 text-[10px]"
                          onClick={() =>
                            void updatePersona(persona.id, { enabled: !persona.enabled })
                          }
                        >
                          {persona.enabled ? '停用' : '啟用'}
                        </Button>
                        {persona.source_type !== 'manual' ? (
                          <Button
                            type="button"
                            size="xs"
                            variant="ghost"
                            className="h-5 px-1 text-[10px]"
                            disabled={busy}
                            onClick={async () => {
                              const result = await refreshPersona(persona.id)
                              if (!result) {
                                toast.error(
                                  useReplySettingsStore.getState().error ?? '更新失敗',
                                )
                                return
                              }
                              toast.success(
                                result.changed
                                  ? `「${result.persona.name}」已更新到最新版本`
                                  : `「${result.persona.name}」的來源內容沒有變化`,
                              )
                            }}
                            title="重新從來源取得（會更新 commit）"
                          >
                            <RefreshCwIcon className="size-3" />
                          </Button>
                        ) : null}
                        <Button
                          type="button"
                          size="xs"
                          variant="ghost"
                          className="h-5 px-1 text-[10px] text-destructive"
                          onClick={async () => {
                            if (await deletePersona(persona.id)) {
                              toast.success(`已刪除「${persona.name}」`)
                            }
                          }}
                        >
                          <Trash2Icon className="size-3" />
                        </Button>
                      </div>
                    </div>
                    {persona.description ? (
                      <p className="text-[10px] leading-snug text-muted-foreground">
                        {persona.description}
                      </p>
                    ) : null}
                    <p className="font-mono text-[10px] text-muted-foreground">
                      {personaSourceLine(persona)}
                    </p>
                    <PersonaProfilePreview persona={persona} />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            關閉
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** 顯示淨化後實際會用到的內容——讓使用者看得出系統採用了什麼。 */
function PersonaProfilePreview({ persona }: { persona: Persona }) {
  const rows: Array<[string, string[]]> = [
    ['思考方式', persona.profile.thinking_style ?? []],
    ['表達習慣', persona.profile.communication_style ?? []],
    ['要避開', persona.profile.avoid ?? []],
  ]
  const shown = rows.filter(([, values]) => values.length > 0)
  if (!shown.length) {
    return (
      <p className="text-[10px] text-amber-600 dark:text-amber-500">
        淨化後沒有留下可用的風格資訊。
      </p>
    )
  }
  return (
    <div className="space-y-0.5">
      {shown.map(([label, values]) => (
        <p key={label} className="text-[10px] leading-snug text-muted-foreground">
          <span className="font-medium">{label}</span>：{values.join('；')}
        </p>
      ))}
      {needsPublicFigureNotice(persona) ? (
        <p className="text-[10px] leading-snug text-amber-600 dark:text-amber-500">
          {PUBLIC_FIGURE_NOTICE}
        </p>
      ) : null}
    </div>
  )
}

// ==========================================================================
// 提示詞 preset 管理
// ==========================================================================

function ReplyPromptManagerDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const replyPrompts = useReplySettingsStore((s) => s.replyPrompts)
  const createReplyPrompt = useReplySettingsStore((s) => s.createReplyPrompt)
  const updateReplyPrompt = useReplySettingsStore((s) => s.updateReplyPrompt)
  const deleteReplyPrompt = useReplySettingsStore((s) => s.deleteReplyPrompt)
  const customPrompt = useDraftStore((s) => s.customPrompt)

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [text, setText] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)

  const handleSubmit = async () => {
    if (editingId !== null) {
      const updated = await updateReplyPrompt(editingId, {
        name: name.trim(),
        description: description.trim(),
        prompt: text,
      })
      if (updated) {
        toast.success(`已更新「${updated.name}」`)
        setEditingId(null)
        setName('')
        setDescription('')
        setText('')
      } else {
        toast.error(useReplySettingsStore.getState().error ?? '更新失敗')
      }
      return
    }
    const created = await createReplyPrompt({
      name: name.trim(),
      description: description.trim(),
      prompt: text,
    })
    if (created) {
      toast.success(`已儲存「${created.name}」`)
      setName('')
      setDescription('')
      setText('')
    } else {
      toast.error(useReplySettingsStore.getState().error ?? '儲存失敗')
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>常用提示詞</DialogTitle>
          <DialogDescription>
            存起來重複使用的回覆要求。套用之後仍然可以在草稿頁微調——送出時以輸入框裡的內容為準。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2 rounded border border-border p-3">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold">
                {editingId === null ? '新增' : '編輯'}
              </span>
              {editingId === null && customPrompt.trim() ? (
                <Button
                  type="button"
                  size="xs"
                  variant="ghost"
                  className="ml-auto h-5 px-1 text-[10px] text-muted-foreground"
                  onClick={() => setText(customPrompt)}
                >
                  帶入草稿頁目前的內容
                </Button>
              ) : null}
              {editingId !== null ? (
                <Button
                  type="button"
                  size="xs"
                  variant="ghost"
                  className="ml-auto h-5 px-1 text-[10px]"
                  onClick={() => {
                    setEditingId(null)
                    setName('')
                    setDescription('')
                    setText('')
                  }}
                >
                  取消編輯
                </Button>
              ) : null}
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="flex flex-col gap-1">
                <Label htmlFor="preset-name" className="text-[11px] text-muted-foreground">
                  名稱
                </Label>
                <Input
                  id="preset-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="我的工程師回覆"
                  className="h-7 text-xs"
                />
              </div>
              <div className="flex flex-col gap-1">
                <Label htmlFor="preset-desc" className="text-[11px] text-muted-foreground">
                  說明（選填）
                </Label>
                <Input
                  id="preset-desc"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="平常回工程團隊使用"
                  className="h-7 text-xs"
                />
              </div>
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="preset-text" className="text-[11px] text-muted-foreground">
                內容
              </Label>
              <Textarea
                id="preset-text"
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={4}
                maxLength={2000}
                className="min-h-20 text-xs"
                placeholder="回覆不要太正式。直接告訴對方目前問題在哪，如果需要他補資料，就明確列出需要哪些資料。"
              />
            </div>
            <Button
              type="button"
              size="sm"
              className="w-full"
              disabled={!name.trim() || !text.trim()}
              onClick={() => void handleSubmit()}
            >
              {editingId === null ? '儲存' : '更新'}
            </Button>
          </div>

          <div className="space-y-2">
            <span className="text-xs font-semibold">已儲存（{replyPrompts.length}）</span>
            {replyPrompts.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">還沒有儲存任何提示詞。</p>
            ) : (
              <div className="max-h-56 space-y-2 overflow-y-auto">
                {replyPrompts.map((preset) => (
                  <div key={preset.id} className="space-y-1 rounded border border-border p-2">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium">{preset.name}</span>
                      <div className="ml-auto flex gap-1">
                        <Button
                          type="button"
                          size="xs"
                          variant="ghost"
                          className="h-5 px-1 text-[10px]"
                          onClick={() => {
                            setEditingId(preset.id)
                            setName(preset.name)
                            setDescription(preset.description)
                            setText(preset.prompt)
                          }}
                        >
                          編輯
                        </Button>
                        <Button
                          type="button"
                          size="xs"
                          variant="ghost"
                          className="h-5 px-1 text-[10px] text-destructive"
                          onClick={async () => {
                            if (await deleteReplyPrompt(preset.id)) {
                              toast.success(`已刪除「${preset.name}」`)
                              if (editingId === preset.id) setEditingId(null)
                            }
                          }}
                        >
                          <Trash2Icon className="size-3" />
                        </Button>
                      </div>
                    </div>
                    {preset.description ? (
                      <p className="text-[10px] text-muted-foreground">{preset.description}</p>
                    ) : null}
                    <p className="whitespace-pre-wrap text-[10px] leading-snug text-muted-foreground">
                      {preset.prompt}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            關閉
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
