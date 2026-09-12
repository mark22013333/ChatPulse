import { useEffect, useState, type ReactNode } from 'react'
import { useTheme } from 'next-themes'
import { MoonIcon, SunIcon } from 'lucide-react'
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

export function ThemeToggle({ label, className }: ThemeToggleProps = {}) {
  const { resolvedTheme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])

  const isDark = !mounted || resolvedTheme === 'dark'
  // 可及名稱要同時說出**現在是什麼**與**按了會變成什麼**：只說
  // 「切換為淺色」的話，看不見畫面的人不知道自己現在在哪一邊。
  // 原本這裡另外掛了一個內容一模一樣的 tooltip，是真重複（規格 §10.3）。
  const ariaLabel = isDark ? '目前深色，切換為淺色' : '目前淺色，切換為深色'
  const toggle = () => setTheme(isDark ? 'light' : 'dark')
  const icon = isDark ? <SunIcon className="size-4 shrink-0" /> : <MoonIcon className="size-4 shrink-0" />

  if (label !== undefined) {
    return (
      <button type="button" aria-label={ariaLabel} onClick={toggle} className={className}>
        {icon}
        {label}
      </button>
    )
  }

  return (
    <Button size="icon-sm" variant="ghost" aria-label={ariaLabel} onClick={toggle}>
      {isDark ? <SunIcon /> : <MoonIcon />}
    </Button>
  )
}
