import { useCallback, useEffect, useRef } from 'react'
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
import { useUiStore } from '@/store/ui'

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
 * 關閉就是回到 router 記下的 `previousHash`（進設定之前所在的位置），
 * 直接貼連結進來時那是 `#/summary`。因此不需要 `returnTo` 參數。
 *
 * 判準是「**這次不一樣**留工作區，**以後都這樣**進設定中心」。
 */
export function SettingsOverlay() {
  const { route, navigate, previousHash } = useRouter()
  const tab = route.settingsTab ?? 'reply'
  const panelRef = useRef<HTMLDivElement>(null)

  // 關閉＝把「整段設定操作」這一筆歷史換回原本的位置。
  //
  // 一定要 replace：不帶的話會再 push 一筆，於是「進設定 → 關閉 → 按返回鍵」
  // 又掉回設定裡。搭配下面分頁列的 replace，整段設定在歷史上只占一筆，
  // 所以不管點過幾個分頁，關閉都只要按一次。
  const close = useCallback(() => {
    navigate(previousHash, { replace: true })
  }, [navigate, previousHash])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      // Esc 要由內而外一層一層關，所以這裡有**兩道**守衛，缺一不可：
      //
      // 1. `defaultPrevented`：內層已經處理掉這個按鍵了。命令面板的 Esc 是
      //    React 合成事件，它 preventDefault 之後原生事件仍會冒泡到 window
      //    上的這個監聽器。**只檢查 paletteOpen 是不夠的**——面板的 close()
      //    是同步的 zustand set，事件走到這裡時 paletteOpen 已經變回 false，
      //    於是設定被一起關掉。2026-09-10 由真瀏覽器 E2E 抓到；元件測試沒
      //    抓到，因為那個測試只設了 store 旗標、沒有掛真正的面板。
      // 2. `paletteOpen`：面板開著、但這個按鍵**不是**它處理的（面板的
      //    handler 掛在輸入框上，焦點跑掉時就不會觸發）。這時設定同樣不該
      //    反應——上面還蓋著一層東西。
      if (event.defaultPrevented) return
      if (useUiStore.getState().paletteOpen) return
      event.stopPropagation()
      close()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [close])

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
                    // 切換設定分頁不是「值得用返回鍵走回去」的導覽，它只該占
                    // 一筆歷史。裸 <a> 每點一次就 push 一筆，於是點五個分頁
                    // 要按五次關閉才出得去。保留 href 讓中鍵開新分頁與螢幕
                    // 閱讀器仍然正確，只攔左鍵。
                    onClick={(event) => {
                      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return
                      event.preventDefault()
                      navigate(hashForSettings(item.id), { replace: true })
                    }}
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
