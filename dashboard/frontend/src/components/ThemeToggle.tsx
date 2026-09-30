import { useEffect, useState, type ReactNode } from 'react'
import { useTheme } from 'next-themes'
import { MonitorIcon, MoonIcon, SunIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface ThemeToggleProps {
  /**
   * 導覽列用：傳入可見標籤時改走「與相鄰項一致」的排版，不用 Button 的圖示尺寸。
   *
   * 為什麼需要：它在左側導覽裡跟「收合」「登出」並排，那兩個都有文字標籤，
   * 只有它是一顆孤立的圖示——展開態下特別突兀。不傳就是原本的純圖示行為。
   */
  label?: ReactNode
  className?: string
}

type ThemeChoice = 'dark' | 'light' | 'system'

/**
 * 三態輪替：深色 → 淺色 → 跟隨系統 → 深色。
 *
 * 2026-10-01 決定：加入「跟隨系統」但**預設仍是深色**（main.tsx 的
 * defaultTheme）。被否決的做法是把預設也改成 system——沒切過主題的同事，
 * 系統若是淺色，下次打開畫面會在沒有任何操作的情況下從深色翻成淺色。
 */
const NEXT: Record<ThemeChoice, ThemeChoice> = { dark: 'light', light: 'system', system: 'dark' }
const NAME: Record<ThemeChoice, string> = { dark: '深色', light: '淺色', system: '跟隨系統' }

export function ThemeToggle({ label, className }: ThemeToggleProps = {}) {
  const { theme, resolvedTheme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])

  // ⚠️ 掛載前 next-themes 還沒讀到 localStorage，theme 是 undefined；
  // 沒有 ThemeProvider 時（部分測試）也一樣。這兩種情況都退回以
  // resolvedTheme 判斷深淺，不要猜成 system。
  const current: ThemeChoice =
    mounted && (theme === 'system' || theme === 'light' || theme === 'dark')
      ? theme
      : !mounted || resolvedTheme === 'dark'
        ? 'dark'
        : 'light'
  const next = NEXT[current]

  // 可及名稱要同時說出**現在是什麼**與**按了會變成什麼**：只說
  // 「切換為淺色」的話，看不見畫面的人不知道自己現在在哪一邊。
  // 跟隨系統時再補上實際生效的深淺，否則「目前跟隨系統」等於沒說畫面長怎樣。
  const currentName =
    current === 'system' ? `跟隨系統（${resolvedTheme === 'dark' ? '深色' : '淺色'}）` : NAME[current]
  const ariaLabel = `目前${currentName}，切換為${NAME[next]}`
  const toggle = () => setTheme(next)

  // 圖示畫的是「按了會變成什麼」，與原本的兩態行為一致
  const Icon = next === 'light' ? SunIcon : next === 'system' ? MonitorIcon : MoonIcon

  if (label !== undefined) {
    return (
      <button type="button" aria-label={ariaLabel} onClick={toggle} className={className}>
        <Icon className="size-4 shrink-0" />
        {label}
      </button>
    )
  }

  return (
    <Button size="icon-sm" variant="ghost" aria-label={ariaLabel} onClick={toggle}>
      <Icon />
    </Button>
  )
}
