import { cn } from '@/lib/utils'
import type { ReactNode } from 'react'

/**
 * 常駐掛載的其中一半（設計規格 §7.2）。
 *
 * **不用 `display:none`**：`SpaceList` 用 `@tanstack/react-virtual`，在
 * `display:none` 的容器裡量到的高度是 0，切回來會重新 measure——畫面閃一下、
 * 捲動位置歸零（2026-09-10 在主從切換上實測過一次，`scrollTop` 從 0 自己
 * 跳到 1296）。改成保留尺寸的絕對定位，並用 React 19 原生的 `inert` 讓看不見
 * 的那一半退出 tab 序與無障礙樹（只靠 `aria-hidden` 擋不住 Tab）。
 *
 * `AppShell` 的主從切換對左欄與工作區整體用的是同一套手法。
 */
export function Pane({ active, children }: { active: boolean; children: ReactNode }) {
  return (
    <div
      className={cn(
        'flex min-h-0 flex-col',
        active ? 'flex-1' : 'pointer-events-none absolute inset-0 -z-10 opacity-0',
      )}
      inert={!active}
    >
      {children}
    </div>
  )
}
