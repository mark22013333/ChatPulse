import { useEffect, useState } from 'react'

/**
 * 版面斷點（設計規格 §12）。
 *
 * - `wide`：≥1280，三欄常駐（證據欄在版面裡）
 * - `medium`：1024–1280，兩欄 ＋ 證據抽屜
 * - `narrow`：768–1024，主從切換
 * - `tiny`：<768，顯示「請用桌機」說明頁
 *
 * 絕大多數的響應式交給 Tailwind 的 CSS 斷點處理，這個 hook 只服務「抽屜
 * 還是常駐」這種**必須用 JS 才知道的**判斷。
 */
export type Breakpoint = 'tiny' | 'narrow' | 'medium' | 'wide'

const QUERIES: [Breakpoint, string][] = [
  ['wide', '(min-width: 1280px)'],
  ['medium', '(min-width: 1024px)'],
  ['narrow', '(min-width: 768px)'],
]

function current(): Breakpoint {
  // 沒有 window 時回傳最寬的那一檔（vitest 是 node 環境；
  // 而且「假設空間夠」比「假設空間不夠」對第一次繪製友善）
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'wide'
  for (const [name, query] of QUERIES) {
    if (window.matchMedia(query).matches) return name
  }
  return 'tiny'
}

export function useBreakpoint(): Breakpoint {
  const [breakpoint, setBreakpoint] = useState<Breakpoint>(current)

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const lists = QUERIES.map(([, query]) => window.matchMedia(query))
    const update = () => setBreakpoint(current())
    for (const list of lists) list.addEventListener('change', update)
    update()
    return () => {
      for (const list of lists) list.removeEventListener('change', update)
    }
  }, [])

  return breakpoint
}
