import { useEffect } from 'react'
import { FileCodeIcon, XIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ENV_LABELS, ENV_ORDER, useCodeProjectStore } from '@/store/codeProjects'
import { useDraftStore } from '@/store/draft'

/**
 * 參考專案的環境勾選矩陣（ADR-0006、規格 §9.2）。
 *
 * 同一個專案可同時勾正式與 UAT——「這是不是 bug」這類問題最有價值的用法
 * 就是比對兩個環境。與 Reference Space 一樣**預設一個都不勾**：自動挑專案
 * 等於讓系統猜答案在哪，那是 ADR-0003 明確拒絕的性質。
 *
 * 一個專案都沒設定時整塊不畫（不是畫一個空清單）。
 */
export function CodeRefPicker() {
  const codeRefs = useDraftStore((s) => s.codeRefs)
  const toggleCodeRef = useDraftStore((s) => s.toggleCodeRef)
  const clearCodeRefs = useDraftStore((s) => s.clearCodeRefs)
  const streaming = useDraftStore((s) => s.streaming)

  const projects = useCodeProjectStore((s) => s.projects)
  const ensureLoaded = useCodeProjectStore((s) => s.ensureLoaded)
  useEffect(() => {
    void ensureLoaded()
  }, [ensureLoaded])

  if (projects.length === 0) return null

  return (
    <div className="shrink-0 space-y-2 border-t border-border px-3 py-2.5">
      <div className="flex items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-xs font-semibold">
          <FileCodeIcon className="size-3.5" aria-hidden />
          參考專案
        </h3>
        <span className="text-2xs text-muted-foreground">已勾選 {codeRefs.length}</span>
        {codeRefs.length > 0 ? (
          <Button size="xs" variant="ghost" className="ml-auto" onClick={clearCodeRefs}>
            <XIcon />
            清空
          </Button>
        ) : null}
      </div>
      <p className="text-2xs leading-relaxed text-muted-foreground">
        同一個專案可同時勾正式與 UAT，草稿會分開講兩邊的差異。
      </p>
      <ul className="space-y-1.5">
        {projects.map((project) => (
          <li key={project.id} className="space-y-1">
            <p className="truncate text-xs font-medium">{project.name}</p>
            <div className="flex flex-wrap gap-1">
              {ENV_ORDER.filter((env) => project.branches[env]).map((env) => {
                const checked = codeRefs.some(
                  (ref) => ref.project_id === project.id && ref.environment === env,
                )
                return (
                  <label
                    key={env}
                    className={`flex cursor-pointer items-center gap-1 rounded border px-1.5 py-0.5 text-2xs ${
                      checked
                        ? 'border-primary bg-primary/10 text-primary'
                        : 'border-border text-muted-foreground'
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="sr-only"
                      checked={checked}
                      disabled={streaming}
                      onChange={() => toggleCodeRef(project.id, env)}
                    />
                    {ENV_LABELS[env]}
                    <code className="metric opacity-70">{project.branches[env]}</code>
                  </label>
                )
              })}
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
