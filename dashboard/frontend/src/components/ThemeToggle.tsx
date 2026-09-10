import { useEffect, useState } from 'react'
import { useTheme } from 'next-themes'
import { MoonIcon, SunIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])

  const isDark = !mounted || resolvedTheme === 'dark'

  return (
    <Button
      size="icon-sm"
      variant="ghost"
      // 可及名稱要同時說出**現在是什麼**與**按了會變成什麼**：只說
      // 「切換為淺色」的話，看不見畫面的人不知道自己現在在哪一邊。
      // 原本這裡另外掛了一個內容一模一樣的 tooltip，是真重複（規格 §10.3）。
      aria-label={isDark ? '目前深色，切換為淺色' : '目前淺色，切換為深色'}
      onClick={() => setTheme(isDark ? 'light' : 'dark')}
    >
      {isDark ? <SunIcon /> : <MoonIcon />}
    </Button>
  )
}
