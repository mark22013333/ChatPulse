import { BookmarkCheckIcon, Loader2Icon } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import {
  AUTO_PROVIDER,
  CUSTOM_MODEL,
  INHERIT_MODEL,
  effectiveModel,
  modelChoices,
  providerLabel,
  resolveModelDraft,
  resolveModelValue,
  useProviderStore,
} from '@/store/providers'

interface ModelSelectProps {
  /** 給 Label 的 htmlFor 用，兩個工作區各自要不同的 id */
  id: string
  /** 串流中就不讓改 */
  disabled?: boolean
  /** 觸發器寬度（Draft 側欄比較窄） */
  triggerClassName?: string
  className?: string
}

/**
 * Model 選擇器，掛在 `ProviderSelect` 旁邊（摘要工作台與 Draft Reply 共用）。
 *
 * 三個刻意的設計：
 * 1. 永遠有一個「跟隨預設」選項——沒有它的話，畫面一載入就得替 Viewer 挑一個
 *    具體 model 送出去，等於讓從沒選過 model 的人突然開始指定 model。
 * 2. 清單之外一定要有「自訂…」：後端給的 `models` 是常用值、不是完整列舉，
 *    擋住輸入等於讓新出的模型永遠用不了。
 * 3. 🔴 供應商是「自動」時停用，而且把**理由寫成看得見的字**。只把它變灰的話，
 *    Viewer 只知道不能點，不知道要先去改隔壁那個下拉——後端會用 400 擋下
 *    `auto` ＋ model 的組合，這裡不擋就會變成一次失敗的串流。
 */
export function ModelSelect({ id, disabled, triggerClassName, className }: ModelSelectProps) {
  const providers = useProviderStore((state) => state.providers)
  const selected = useProviderStore((state) => state.selected)
  const savedModels = useProviderStore((state) => state.savedModels)
  const modelSelection = useProviderStore((state) => state.modelSelection)
  const modelDrafts = useProviderStore((state) => state.modelDrafts)
  const savingModel = useProviderStore((state) => state.savingModel)
  const setModel = useProviderStore((state) => state.setModel)
  const setModelDraft = useProviderStore((state) => state.setModelDraft)
  const saveModelAsDefault = useProviderStore((state) => state.saveModelAsDefault)

  const isAuto = !selected || selected === AUTO_PROVIDER
  const provider = providers.find((item) => item.name === selected)
  const choices = modelChoices(providers, selected)

  // 「自動」時不讀任何一個供應商的狀態：那時下拉是停用的，顯示誰的偏好都是誤導
  const value = isAuto
    ? INHERIT_MODEL
    : resolveModelValue(providers, savedModels, modelSelection, selected)
  const draft = isAuto ? '' : resolveModelDraft(providers, savedModels, modelDrafts, selected)
  const current = isAuto
    ? null
    : effectiveModel(providers, savedModels, modelSelection, modelDrafts, selected)

  /**
   * 不帶 `model` 欄位時，**後端實際會用的那個裸值**。
   *
   * 🔴 這裡不可以只寫 `provider.model_id`。後端的生效順序是
   * 「body 的 model → Viewer 的偏好 → config 預設」（契約第三節），所以
   * Viewer 存過偏好時，不帶 model 跑的是**偏好**、不是 config 預設。
   * 只讀 `model_id` 的話畫面會寫出一個根本不會被執行的值——那比不寫更糟：
   * 原本 Viewer 還會懷疑，看到一個具體的數字之後他就不查了。
   *
   * 判準很簡單：畫面上括號裡的值，必須跟真的跑起來的值是同一個。
   */
  const fallbackModel = isAuto ? '' : (savedModels[selected] ?? provider?.model_id ?? '')

  const inheritLabel = fallbackModel ? `跟隨預設（${fallbackModel}）` : '跟隨預設'

  /**
   * 選了「自訂」卻沒填字——這次其實**不會**指定 model，退回供應商的預設。
   *
   * 🔴 這個狀態非說不可：三個指標本來都不會說話——request 靜默不帶 `model`、
   * 觸發器寫著「自訂」看起來像已經設定、`current` 與已存偏好都是 `null` 所以
   * 連「設為預設」都不出現。Viewer 因此以為自己指定了 model，而畫面上沒有
   * 任何一處反駁他。專案硬規則點名的靜默降級就是這種形態。
   */
  const customBlank = !isAuto && value === CUSTOM_MODEL && !draft.trim()

  // Base UI 的 Select 用 items 這份對照表決定觸發器上顯示的文字。
  // 自訂值不在清單裡，所以要自己組一行，否則觸發器上只會寫「自訂…」，
  // 看不出實際會送哪個 model——空白時更要寫出「尚未填」，不能裝作有設定。
  const items: Record<string, string> = {
    [INHERIT_MODEL]: inheritLabel,
    ...Object.fromEntries(choices.map((item) => [item, item])),
    [CUSTOM_MODEL]: draft.trim() ? `自訂：${draft.trim()}` : '自訂（尚未填）',
  }

  // 目前選擇還沒存成偏好時，才顯示「設為預設」（與 ProviderSelect 同一套互動）
  const canSaveDefault = !isAuto && current !== (savedModels[selected] ?? null)

  const handleSave = async () => {
    if (isAuto) return
    const ok = await saveModelAsDefault(selected)
    if (ok) {
      const name = providerLabel(providers, selected)
      toast.success(
        current
          ? `已把「${current}」設為 ${name} 的預設 model`
          : `已清除 ${name} 的 model 偏好，之後沿用伺服器預設`,
      )
    } else {
      toast.error(useProviderStore.getState().error ?? '儲存偏好失敗')
    }
  }

  return (
    <div className={cn('flex flex-col gap-1', className)}>
      <div className="flex items-center gap-1">
        <Label htmlFor={id} className="text-xs text-muted-foreground">
          模型
        </Label>
        {canSaveDefault ? (
          <Button
            type="button"
            size="xs"
            variant="ghost"
            className="h-4 px-1 text-2xs text-muted-foreground"
            disabled={savingModel}
            onClick={() => void handleSave()}
          >
            {savingModel ? (
              <Loader2Icon className="size-3 animate-spin" />
            ) : (
              <BookmarkCheckIcon className="size-3" />
            )}
            設為預設
          </Button>
        ) : null}
      </div>

      <Select
        items={items}
        value={value}
        onValueChange={(next) => {
          // Base UI 的 Select 清除選擇時會給 null，那種情況不要動選擇
          if (!next || isAuto) return
          setModel(selected, next as string)
        }}
      >
        <SelectTrigger
          id={id}
          size="sm"
          className={cn('w-44', triggerClassName)}
          disabled={disabled || isAuto}
          aria-describedby={isAuto ? `${id}-auto-hint` : undefined}
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent className="w-auto max-w-96 min-w-72">
          <SelectItem value={INHERIT_MODEL}>
            <span className="flex w-full flex-col gap-0.5 whitespace-normal">
              <span className="font-medium">{inheritLabel}</span>
              <span className="text-2xs leading-snug text-muted-foreground">
                不指定 model，用這個供應商自己的預設。
              </span>
            </span>
          </SelectItem>

          {choices.map((item) => (
            <SelectItem key={item} value={item}>
              <span className="flex w-full flex-col gap-0.5 whitespace-normal">
                <span className="metric font-medium">{item}</span>
              </span>
            </SelectItem>
          ))}

          <SelectItem value={CUSTOM_MODEL}>
            <span className="flex w-full flex-col gap-0.5 whitespace-normal">
              <span className="font-medium">自訂…</span>
              <span className="text-2xs leading-snug text-muted-foreground">
                上面是常用的幾個，不是全部。要用清單外的 model 就自己打。
              </span>
            </span>
          </SelectItem>
        </SelectContent>
      </Select>

      {/* 停用的理由要看得見。只有灰掉的話，Viewer 不會知道問題出在隔壁那個下拉 */}
      {isAuto ? (
        <p id={`${id}-auto-hint`} className="max-w-72 text-2xs leading-snug text-caution">
          AI 供應商選「自動」時不能指定 model：伺服器會自己挑供應商，你指定的
          model 未必適用於它。要選 model 請先在左邊挑一個具體的供應商。
        </p>
      ) : null}

      {!isAuto && value === CUSTOM_MODEL ? (
        <Input
          id={`${id}-custom`}
          value={draft}
          onChange={(event) => setModelDraft(selected, event.target.value)}
          disabled={disabled}
          placeholder="直接輸入 model 名稱"
          aria-label="自訂 model 名稱"
          aria-describedby={customBlank ? `${id}-blank-hint` : undefined}
          className={cn('h-7 w-44', triggerClassName)}
        />
      ) : null}

      {/* 空白的自訂框＝這次沒有指定 model。說出來，別讓 Viewer 以為設定生效了。
          括號裡寫的是 fallbackModel（偏好優先），不是 config 預設——理由見它的註解 */}
      {customBlank ? (
        <p id={`${id}-blank-hint`} className="max-w-72 text-2xs leading-snug text-caution">
          還沒填 model 名稱，這次不會指定 model
          {fallbackModel ? `，實際會用 ${fallbackModel}` : ''}。
        </p>
      ) : null}
    </div>
  )
}
