import { useEffect, useState } from 'react'
import { DownloadIcon, Loader2Icon, RefreshCwIcon, Trash2Icon } from 'lucide-react'
import { toast } from 'sonner'
import { PersonaProfileView } from '@/components/settings/PersonaProfileView'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { personaSourceLine, useReplySettingsStore } from '@/store/replySettings'

// ==========================================================================
// Persona 管理
// ==========================================================================

export function PersonasPage() {
  const personas = useReplySettingsStore((s) => s.personas)
  const busy = useReplySettingsStore((s) => s.busy)
  const loaded = useReplySettingsStore((s) => s.loaded)
  const load = useReplySettingsStore((s) => s.load)
  const importPersona = useReplySettingsStore((s) => s.importPersona)
  const refreshPersona = useReplySettingsStore((s) => s.refreshPersona)
  const deletePersona = useReplySettingsStore((s) => s.deletePersona)
  const updatePersona = useReplySettingsStore((s) => s.updatePersona)

  // 這一頁必須自己載。三條路徑會**直接**落在這裡而沒有先經過任何會 load 的
  // 頁面：`#/settings/personas` 深連結、停在這一頁按重新整理、從書籤進來。
  // 少了這行的後果不是「慢一點才出現」，是穩定地顯示「還沒有匯入任何
  // Persona。」——與「被刪掉了」長得一模一樣（2026-09-11 實測）。
  // `load` 自己有 `loaded`／`loading` 的守衛，重複呼叫不會多打端點。
  useEffect(() => {
    void load()
  }, [load])

  const [mode, setMode] = useState<'github' | 'url'>('github')
  const [repository, setRepository] = useState('')
  const [slug, setSlug] = useState('')
  const [url, setUrl] = useState('')
  const [name, setName] = useState('')
  // 匯入失敗的訊息**留在畫面上**，不只用 toast。
  //
  // 409 PERSONA_INVALID 現在會帶 `describe_unusable()` 的診斷（後端
  // `_persona_import_result`），內容是「這份檔案讀到哪些章節、可用的章節名是
  // 什麼」——那是要**照著改**的資訊，而 sonner 預設 4 秒就收掉。塞在 toast
  // 裡等於算得出診斷卻沒真的給使用者，跟不給差不多。
  // 用本地 state 而不是 store.error：store.error 是所有操作共用的，
  // 拿它會把「刪除失敗」顯示在匯入表單裡。
  const [importError, setImportError] = useState<string | null>(null)

  const handleImport = async () => {
    const body =
      mode === 'github'
        ? { source_type: 'github', repository: repository.trim(), persona: slug.trim() }
        : { source_type: 'url', url: url.trim() }
    const persona = await importPersona({ ...body, name: name.trim() || undefined })
    if (persona) {
      setImportError(null)
      toast.success(`已匯入 Persona「${persona.name}」`)
      setRepository('')
      setSlug('')
      setUrl('')
      setName('')
    } else {
      const message = useReplySettingsStore.getState().error ?? '匯入失敗'
      setImportError(message)
      // toast 只當「發生了什麼事」的即時訊號，細節看下面那塊
      toast.error('匯入失敗，原因顯示在匯入表單下方')
    }
  }

  const canImport =
    !busy && (mode === 'github' ? repository.trim() && slug.trim() : url.trim().length > 0)

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold">Persona</h2>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          Persona 是「借用某個人的思考框架與表達習慣來寫回覆」，不是扮演那個人。
          匯入時會固定版本（記下 commit），遠端之後改了也不會影響已產生的回話。
        </p>
      </div>

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
                  className="h-5 px-2 text-2xs"
                  // 換模式＝「我改用另一種方式試」，上一個模式的錯誤訊息
                  // 留著只會誤導（它講的是另一種輸入的問題）
                  onClick={() => {
                    setMode(m)
                    setImportError(null)
                  }}
                >
                  {m === 'github' ? 'Repository' : '網址'}
                </Button>
              ))}
            </div>
          </div>

          {mode === 'github' ? (
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="flex flex-col gap-1">
                <Label htmlFor="persona-repo" className="text-xs text-muted-foreground">
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
                <Label htmlFor="persona-slug" className="text-xs text-muted-foreground">
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
              <Label htmlFor="persona-url" className="text-xs text-muted-foreground">
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
            <Label htmlFor="persona-name" className="text-xs text-muted-foreground">
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
          {importError ? (
            // `role="alert"` 而不是只有顏色：這塊是唯一說明「為什麼匯不進來」
            // 的地方，用讀螢幕的人也要拿得到。整段可選取，因為使用者常常要
            // 把章節名複製去改檔案。
            <div
              role="alert"
              className="space-y-1 rounded border border-destructive/40 bg-destructive/5 p-2"
            >
              <p className="text-2xs font-medium text-destructive">匯入失敗</p>
              <p className="text-2xs leading-relaxed text-foreground">{importError}</p>
            </div>
          ) : null}

          <p className="text-2xs leading-relaxed text-muted-foreground">
            匯入的內容會經過淨化：角色扮演指令、工具呼叫、讀檔要求、以及「不知道就推測」
            這類授權一律不採用。只有表達與思考風格會被保留。
          </p>
        </div>

        <div className="space-y-2">
          <span className="text-xs font-semibold">已匯入（{personas.length}）</span>
          {personas.length === 0 ? (
            // 「還沒載完」與「真的沒有」要分開講。兩者都是空清單，但前者說
            // 「還沒有匯入任何 Persona」是**假話**，而使用者無從分辨。
            !loaded ? (
              <p className="flex items-center gap-1 text-xs text-muted-foreground">
                <Loader2Icon className="size-3 animate-spin" aria-hidden />
                正在讀取已匯入的 Persona…
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">還沒有匯入任何 Persona。</p>
            )
          ) : (
            <div className="max-h-64 space-y-2 overflow-y-auto">
              {personas.map((persona) => (
                <div key={persona.id} className="space-y-1 rounded border border-border p-2">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium">{persona.name}</span>
                    {!persona.enabled ? (
                      <span className="rounded border border-border px-1 py-px text-2xs text-muted-foreground">
                        已停用
                      </span>
                    ) : null}
                    <div className="ml-auto flex gap-1">
                      <Button
                        type="button"
                        size="xs"
                        variant="ghost"
                        className="h-5 px-1 text-2xs"
                        onClick={() => void updatePersona(persona.id, { enabled: !persona.enabled })}
                      >
                        {persona.enabled ? '停用' : '啟用'}
                      </Button>
                      {persona.source_type !== 'manual' ? (
                        <Button
                          type="button"
                          size="xs"
                          variant="ghost"
                          className="h-5 px-1 text-2xs"
                          disabled={busy}
                          onClick={async () => {
                            const result = await refreshPersona(persona.id)
                            if (!result) {
                              toast.error(useReplySettingsStore.getState().error ?? '更新失敗')
                              return
                            }
                            toast.success(
                              result.changed
                                ? `「${result.persona.name}」已更新到最新版本`
                                : `「${result.persona.name}」的來源內容沒有變化`,
                            )
                          }}
                          // 這顆按鈕只有一個圖示，說明是它唯一的可及名稱來源。
                          // 原本那句活在 tooltip 裡，鍵盤與觸控使用者拿不到
                          // （規格 §10.3）。commit 會不會變是重點——那決定
                          // 「明天產生的草稿還是不是同樣行為」。
                          aria-label={`重新從來源取得「${persona.name}」，會更新 commit`}
                        >
                          <RefreshCwIcon className="size-3" aria-hidden />
                        </Button>
                      ) : null}
                      <Button
                        type="button"
                        size="xs"
                        variant="ghost"
                        className="h-5 px-1 text-2xs text-destructive"
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
                    <p className="text-2xs leading-snug text-muted-foreground">
                      {persona.description}
                    </p>
                  ) : null}
                  <p className="metric text-2xs text-muted-foreground">
                    {personaSourceLine(persona)}
                  </p>
                  <PersonaProfileView persona={persona} />
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
