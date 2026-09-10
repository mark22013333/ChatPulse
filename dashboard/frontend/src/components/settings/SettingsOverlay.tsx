import { useEffect, useRef } from 'react'
import { XIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { CodeProjectsPage } from '@/components/settings/CodeProjectsPage'
import { DataPage } from '@/components/settings/DataPage'
import { PersonasPage } from '@/components/settings/PersonasPage'
import { ReplyDefaultsPage } from '@/components/settings/ReplyDefaultsPage'
import { ReplyPromptsPage } from '@/components/settings/ReplyPromptsPage'
import { SpacePrefsPage } from '@/components/settings/SpacePrefsPage'
import { hashForSettings, type SettingsTab } from '@/lib/route'
import { cn } from '@/lib/utils'
import { useRouter } from '@/router/useRouter'

/** 分頁順序＝從「最常改」到「最少改」。診斷頁不在這裡，它在登入 gate 之前。 */
const TABS: { id: SettingsTab; label: string; hint: string }[] = [
  { id: 'reply', label: '回覆預設值', hint: '口氣、Persona、提示詞、潤稿' },
  { id: 'personas', label: 'Persona', hint: '匯入、更新、看淨化掉了什麼' },
  { id: 'prompts', label: '常用提示詞', hint: '存起來重複使用的提示' },
  { id: 'code-projects', label: '參考專案', hint: '程式碼佐證的分支對應' },
  { id: 'spaces', label: 'Space', hint: '釘選常用的、給私訊取名' },
  { id: 'data', label: '資料', hint: '用量與歷史 Summary' },
]

function PageFor({ tab }: { tab: SettingsTab }) {
  switch (tab) {
    case 'personas':
      return <PersonasPage />
    case 'prompts':
      return <ReplyPromptsPage />
    case 'code-projects':
      return <CodeProjectsPage />
    case 'spaces':
      return <SpacePrefsPage />
    case 'data':
      return <DataPage />
    case 'diagnostics':
      // 診斷頁排在登入 gate 之前，由 App 直接渲染，不會走到這裡
      return null
    case 'reply':
    default:
      return <ReplyDefaultsPage />
  }
}

/**
 * 設定中心（設計規格 §8）。
 *
 * 這是一個**覆蓋層路由**：兩個工作台仍掛在後面，串流不會中斷、狀態不會掉。
 * 關閉就是 `history.back()`；直接貼連結進來（沒有上一頁）才 fallback 到摘要。
 * 因此不需要 `returnTo` 參數。
 *
 * 判準是「**這次不一樣**留工作區，**以後都這樣**進設定中心」。
 */
export function SettingsOverlay() {
  const { route, navigate } = useRouter()
  const tab = route.settingsTab ?? 'reply'
  const panelRef = useRef<HTMLDivElement>(null)
  const openedAt = useRef(window.history.length)

  const close = () => {
    // 有上一頁就退回去（使用者原本在看的那個 Space／Mention 還在）；
    // 直接貼連結進來的話退不回去，落到摘要工作台
    if (window.history.length > openedAt.current || window.history.state !== null) {
      window.history.back()
    } else {
      navigate('#/summary', { replace: true })
    }
  }

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        close()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
    // close 每次 render 都是新的，但它只讀 ref 與 navigate，不需要進相依
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 開啟時把焦點移進面板，鍵盤使用者才不會還停在後面的工作台上
  useEffect(() => {
    panelRef.current?.focus()
  }, [])

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-background"
      role="dialog"
      aria-modal="true"
      aria-label="設定"
    >
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line px-gutter">
        <h1 className="text-sm font-semibold tracking-tight">設定</h1>
        <p className="text-fg-dim hidden text-xs sm:block">
          這裡改的是「以後每次」的預設值
        </p>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={close}>
          <XIcon />
          關閉
        </Button>
      </header>

      <div className="flex min-h-0 flex-1">
        <nav
          aria-label="設定分頁"
          className="w-inbox shrink-0 overflow-y-auto border-r border-line p-2"
        >
          <ul className="space-y-0.5">
            {TABS.map((item) => {
              const active = tab === item.id
              return (
                <li key={item.id}>
                  <a
                    href={hashForSettings(item.id)}
                    aria-current={active ? 'page' : undefined}
                    className={cn(
                      'block rounded-lg px-2.5 py-2 transition-colors',
                      active
                        ? 'bg-signal-wash text-signal'
                        : 'text-foreground hover:bg-muted',
                    )}
                  >
                    <span className="block text-sm font-medium">{item.label}</span>
                    <span
                      className={cn(
                        'text-2xs block',
                        active ? 'text-signal/80' : 'text-fg-dim',
                      )}
                    >
                      {item.hint}
                    </span>
                  </a>
                </li>
              )
            })}
          </ul>
        </nav>

        <main
          ref={panelRef}
          tabIndex={-1}
          className="min-w-0 flex-1 overflow-y-auto outline-none"
        >
          <div className="mx-auto max-w-3xl px-gutter py-6">
            <PageFor tab={tab} />
          </div>
        </main>
      </div>
    </div>
  )
}
