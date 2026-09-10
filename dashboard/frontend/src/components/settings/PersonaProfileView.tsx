import { needsPublicFigureNotice, PUBLIC_FIGURE_NOTICE } from '@/store/replySettings'
import type { Persona } from '@/lib/types'

/** 顯示淨化後實際會用到的內容——讓使用者看得出系統採用了什麼。 */
export function PersonaProfileView({ persona }: { persona: Persona }) {
  const rows: Array<[string, string[]]> = [
    ['思考方式', persona.profile.thinking_style ?? []],
    ['表達習慣', persona.profile.communication_style ?? []],
    ['要避開', persona.profile.avoid ?? []],
  ]
  const shown = rows.filter(([, values]) => values.length > 0)
  if (!shown.length) {
    return (
      <p className="text-[10px] text-amber-600 dark:text-amber-500">
        淨化後沒有留下可用的風格資訊。
      </p>
    )
  }
  return (
    <div className="space-y-0.5">
      {shown.map(([label, values]) => (
        <p key={label} className="text-[10px] leading-snug text-muted-foreground">
          <span className="font-medium">{label}</span>：{values.join('；')}
        </p>
      ))}
      {needsPublicFigureNotice(persona) ? (
        <p className="text-[10px] leading-snug text-amber-600 dark:text-amber-500">
          {PUBLIC_FIGURE_NOTICE}
        </p>
      ) : null}
    </div>
  )
}
