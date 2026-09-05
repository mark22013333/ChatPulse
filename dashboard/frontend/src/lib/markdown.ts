import { marked } from 'marked'

marked.setOptions({ gfm: true, breaks: true })

/** 只做最低限度的防護：移除 script/iframe 與行內事件屬性。內容來源是自家後端的 Gemini 輸出。 */
function sanitize(html: string): string {
  return html
    .replace(/<\s*(script|iframe|object|embed)[^>]*>[\s\S]*?<\s*\/\s*\1\s*>/gi, '')
    .replace(/<\s*(script|iframe|object|embed)[^>]*\/?>/gi, '')
    .replace(/\son\w+\s*=\s*"[^"]*"/gi, '')
    .replace(/\son\w+\s*=\s*'[^']*'/gi, '')
    .replace(/javascript:/gi, '')
}

export function renderMarkdown(source: string): string {
  if (!source) return ''
  const html = marked.parse(source, { async: false })
  return sanitize(typeof html === 'string' ? html : '')
}
