import { useMemo } from 'react'
import { cn } from '@/lib/utils'
import { renderMarkdown } from '@/lib/markdown'

interface MarkdownProps {
  source: string
  className?: string
  /** 串流中時在結尾顯示打字游標 */
  typing?: boolean
}

export function Markdown({ source, className, typing = false }: MarkdownProps) {
  const html = useMemo(() => renderMarkdown(source), [source])
  return (
    <div
      className={cn('markdown-body', typing && 'typing-cursor', className)}
      // 內容來自自家後端的 Gemini 輸出，renderMarkdown 已移除 script/事件屬性
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
