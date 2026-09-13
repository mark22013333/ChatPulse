import { useEffect } from 'react'
import { FileCodeIcon, XIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { parseCodeTerms } from '@/lib/codeTerms'
import { ENV_LABELS, ENV_ORDER, useCodeProjectStore } from '@/store/codeProjects'
import { useDraftStore } from '@/store/draft'

/**
 * 手動指定檢索關鍵字。
 *
 * 後端的自動抽詞是**刻意做笨**的啟發式（ADR-0006）：只抓 snake_case／
 * camelCase 這類散文不會出現的形狀。抽不準時會白跑一次，而這個輸入框就是
 * 補償手段——填了就**完全取代**自動抽詞。
 *
 * 回饋迴路是完整的：證據欄的參考專案那幾列會回顯「實際搜了哪些關鍵字」，
 * 所以覆寫有沒有生效、搜錯了沒，使用者在模型開口之前就看得到。
 */
function CodeTermsInput() {
  const codeTerms = useDraftStore((s) => s.codeTerms)
  const setCodeTerms = useDraftStore((s) => s.setCodeTerms)
  const streaming = useDraftStore((s) => s.streaming)

  const parsed = parseCodeTerms(codeTerms)

  return (
    <div className="space-y-1">
      <Label htmlFor="draft-code-terms" className="text-2xs text-muted-foreground">
        自己指定檢索關鍵字（選填）
      </Label>
      <Input
        id="draft-code-terms"
        value={codeTerms}
        onChange={(event) => setCodeTerms(event.target.value)}
        disabled={streaming}
        placeholder="例：sendPush retryCount"
        className="h-7"
        aria-describedby="draft-code-terms-hint"
      />
      <p id="draft-code-terms-hint" className="text-2xs leading-relaxed text-muted-foreground">
        {parsed.length > 0
          ? // 把切出來的結果講清楚。逗號打成全角、或誤以為要打句子的人，
            // 在按下產生之前就看得出來自己填了幾個詞
            `會用這 ${parsed.length} 個關鍵字搜，取代系統自動抽的：${parsed.join('、')}`
          : '留空就讓系統自己從問題裡抽。抽不準時填這裡，用逗號或空白分隔。'}
      </p>
    </div>
  )
}

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
      {/* 手動指定關鍵字（ADR-0006 的逃生門）。只在真的勾了專案時才出現——
          沒勾專案的話後端根本不會搜，多一個沒作用的輸入框只會讓人困惑。 */}
      {codeRefs.length > 0 ? <CodeTermsInput /> : null}

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
