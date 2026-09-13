import { AlertCircleIcon, KeyRoundIcon, Loader2Icon, LogInIcon } from 'lucide-react'
import { PulseMark } from '@/app/PulseMark'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAuthStore } from '@/store/auth'

/**
 * 未登入畫面（規格 6 節前置、契約認證段）。
 * 兩個動作：Google 登入、匯入既有憑證（僅當 can_bootstrap 為 true）。
 */
export function LoginScreen() {
  const status = useAuthStore((state) => state.status)
  const login = useAuthStore((state) => state.login)
  const bootstrap = useAuthStore((state) => state.bootstrap)
  const loginPending = useAuthStore((state) => state.loginPending)
  const bootstrapPending = useAuthStore((state) => state.bootstrapPending)
  const error = useAuthStore((state) => state.error)
  const busy = loginPending || bootstrapPending

  return (
    // 外層只負責捲、不居中；居中放在 min-h-full 的內層。
    // **`items-center` 不可以與 `overflow-y-auto` 放在同一層**：內容比容器高時
    // 居中會把溢出平分到上下，而 `scrollTop` 不能為負，卡片上緣就永遠捲不到。
    // 2026-09-11 實測：1000×320 時卡片上緣在 −32px、怎麼捲都上不去。
    <div className="h-full overflow-y-auto bg-background">
      <div className="flex min-h-full items-center justify-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <div className="mb-2 flex items-center gap-2.5">
            <span className="flex size-9 items-center justify-center rounded-lg bg-signal-wash text-signal ring-1 ring-signal-line">
              <PulseMark className="size-4.5" />
            </span>
            <div>
              <CardTitle className="text-base">ChatPulse</CardTitle>
              <CardDescription className="text-xs">
                Google Chat 摘要與 Mention 收件匣
              </CardDescription>
            </div>
          </div>
          <CardDescription>
            以你自己的 Google 帳號授權後，才看得到你已加入的 Space。ChatPulse 不會主動加入任何
            Space，也不會以機器人身分發話。
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-3">
          {error ? (
            <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
              <AlertCircleIcon className="mt-0.5 size-3.5 shrink-0" />
              <span className="leading-relaxed">{error}</span>
            </div>
          ) : null}

          <Button className="w-full" size="lg" onClick={() => void login()} disabled={busy}>
            {loginPending ? <Loader2Icon className="animate-spin" /> : <LogInIcon />}
            使用 Google 登入
          </Button>

          {loginPending ? (
            <p className="rounded-lg border border-border bg-muted/40 p-3 text-xs leading-relaxed text-muted-foreground">
              已在<strong className="text-foreground">伺服器所在的機器</strong>開啟瀏覽器等待授權。
              請到那台機器完成 Google 授權流程，這裡會自動接手；逾時上限約 3 分鐘。
            </p>
          ) : null}

          {status?.can_bootstrap ? (
            <>
              <div className="flex items-center gap-3 py-1">
                <span className="h-px flex-1 bg-border" />
                <span className="text-xs text-muted-foreground">或</span>
                <span className="h-px flex-1 bg-border" />
              </div>
              <Button
                className="w-full"
                variant="outline"
                size="lg"
                onClick={() => void bootstrap()}
                disabled={busy}
              >
                {bootstrapPending ? <Loader2Icon className="animate-spin" /> : <KeyRoundIcon />}
                匯入既有憑證
              </Button>
              <p className="text-xs leading-relaxed text-muted-foreground">
                把這台機器上既有的授權檔匯入成你的身分。適用於在 ChatPulse 加入 Google
                登入之前就設定過的環境。匯入後若認不出你是誰，診斷頁有排查步驟。
              </p>
            </>
          ) : null}

          {status ? (
            <p className="pt-1 text-xs text-muted-foreground">
              目前已授權的 Viewer 數：{status.viewer_count}
            </p>
          ) : null}
        </CardContent>
      </Card>
      </div>
    </div>
  )
}
