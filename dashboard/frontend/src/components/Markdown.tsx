import { useMemo, useRef } from 'react'
import { cn } from '@/lib/utils'
import { renderMarkdown } from '@/lib/markdown'

interface MarkdownProps {
  source: string
  className?: string
  /** 串流中時在結尾顯示打字游標 */
  typing?: boolean
  /**
   * 這份內容目前看得見嗎。預設 true。
   *
   * 兩個工作台改成常駐掛載之後（設計規格 §7.2），看不見的那一半仍然會收到
   * 每一個串流 chunk。`marked` 每次都是整段重 parse，內容越長越貴——而在此
   * 之前的條件渲染「意外地」避開了這件事：卸載了就不重繪。
   *
   * `active={false}` 時停止跟著串流更新（畫面凍在最後一次結果），切回來時
   * 一次補上。**只影響顯示，不影響 store 裡累積的內容**，所以切回來看到的
   * 一定是完整的。
   */
  active?: boolean
}

export function Markdown({ source, className, typing = false, active = true }: MarkdownProps) {
  const lastRendered = useRef('')
  // 看不見的時候沿用上一次的結果，不重新 parse。
  // 注意 useMemo 的相依要含 active——切回來那一刻必須用當下的 source 重算。
  const html = useMemo(() => {
    if (!active) return lastRendered.current
    lastRendered.current = renderMarkdown(source)
    return lastRendered.current
  }, [source, active])

  return (
    <div
      className={cn('markdown-body', typing && active && 'typing-cursor', className)}
      // **這裡絕對不可以加 aria-live**（設計規格 §10.6）。每個 chunk 都重寫
      // innerHTML，設了等於整段從頭念一次、念到一半又被下一個 chunk 打斷
      // ——比完全不宣告更糟。狀態層的里程碑宣告在 AppShell 的 role="status"，
      // 這裡只負責說「這塊正在被寫」。
      aria-busy={typing && active ? true : undefined}
      // 內容來自自家後端的 Gemini 輸出，renderMarkdown 已移除 script/事件屬性
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
