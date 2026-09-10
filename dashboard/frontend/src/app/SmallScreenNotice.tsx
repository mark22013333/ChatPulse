import { useState } from 'react'
import { MonitorIcon } from 'lucide-react'
import { PulseMark } from '@/app/PulseMark'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { Button } from '@/components/ui/button'
import { hashForSettings } from '@/lib/route'
import { useRouter } from '@/router/useRouter'

const STORAGE_KEY = 'chatpulse.smallScreenAck'

function alreadyAcked(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

/**
 * <768px 的說明頁（設計規格 §12）。
 *
 * 這不是「不支援」的推託，是一個誠實的判斷：Draft Reply 的整個價值在於
 * **送出前看得清楚它建立在什麼之上**——分支名、commit sha、命中的檔案路徑、
 * 一整段脈絡分析。那些東西在 375px 寬的螢幕上讀不了，而讀不了就等於在不
 * 知情的狀況下送出不可撤回的訊息。
 *
 * 但**擋死是錯的**：人在通勤時想確認「還有幾則沒回」是合理的。所以給一個
 * 存在 localStorage 的逃生門。
 */
export function SmallScreenNotice({ children }: { children: React.ReactNode }) {
  const [acked, setAcked] = useState(alreadyAcked)
  const breakpoint = useBreakpoint()
  const { navigate } = useRouter()

  // 768px 以上一律直接放行，這一頁根本不掛
  if (breakpoint !== 'tiny' || acked) return <>{children}</>

  const ack = () => {
    try {
      localStorage.setItem(STORAGE_KEY, '1')
    } catch {
      // 存不進去就只是這一次生效，不影響使用
    }
    setAcked(true)
  }

  return (
    <div className="bg-background text-foreground flex min-h-dvh flex-col px-gutter py-10">
      <div className="mx-auto w-full max-w-sm space-y-5">
        <div className="flex items-center gap-2">
          <span className="bg-signal-wash text-signal ring-signal-line flex size-7 items-center justify-center rounded-md ring-1">
            <PulseMark className="size-4" />
          </span>
          <span className="font-semibold tracking-tight">ChatPulse</span>
        </div>

        <div className="space-y-2">
          <h1 className="text-md flex items-center gap-2 font-semibold">
            <MonitorIcon className="size-5" aria-hidden />
            這個畫面需要比較寬的螢幕
          </h1>
          <p className="text-fg-dim text-sm leading-relaxed">
            Draft Reply 送出之後無法撤回，所以送出前要看得清楚它建立在什麼之上——
            分支名、commit sha、命中的檔案路徑、一整段脈絡分析。這些在手機寬度下
            讀不了，而讀不了就等於在不知情的狀況下送出。
          </p>
        </div>

        <div className="border-line-evidence space-y-2 border-t pt-4">
          <p className="text-sm font-medium">這台裝置仍然可以做的事</p>
          <ul className="text-fg-dim space-y-1 text-sm">
            <li>· 看服務狀態與採集器有沒有在跑</li>
            <li>· 確認還有幾則 Mention 沒處理</li>
          </ul>
          <Button
            variant="outline"
            size="sm"
            className="mt-1"
            onClick={() => navigate(hashForSettings('diagnostics'))}
          >
            開啟診斷頁
          </Button>
        </div>

        <div className="border-line space-y-2 border-t pt-4">
          <Button variant="ghost" size="sm" onClick={ack}>
            我知道，仍要繼續
          </Button>
          <p className="text-fg-subtle text-2xs">
            選了之後這台裝置就不會再提醒。版面會很擠，但不會擋你。
          </p>
        </div>
      </div>
    </div>
  )
}
