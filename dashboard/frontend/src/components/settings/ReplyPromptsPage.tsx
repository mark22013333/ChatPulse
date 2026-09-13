import { useState } from 'react'
import { Trash2Icon } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useDraftStore } from '@/store/draft'
import { useReplySettingsStore } from '@/store/replySettings'

// ==========================================================================
// 提示詞 preset 管理
// ==========================================================================

export function ReplyPromptsPage() {
  const replyPrompts = useReplySettingsStore((s) => s.replyPrompts)
  const createReplyPrompt = useReplySettingsStore((s) => s.createReplyPrompt)
  const updateReplyPrompt = useReplySettingsStore((s) => s.updateReplyPrompt)
  const deleteReplyPrompt = useReplySettingsStore((s) => s.deleteReplyPrompt)
  const customPrompt = useDraftStore((s) => s.customPrompt)

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [text, setText] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)

  const handleSubmit = async () => {
    if (editingId !== null) {
      const updated = await updateReplyPrompt(editingId, {
        name: name.trim(),
        description: description.trim(),
        prompt: text,
      })
      if (updated) {
        toast.success(`已更新「${updated.name}」`)
        setEditingId(null)
        setName('')
        setDescription('')
        setText('')
      } else {
        toast.error(useReplySettingsStore.getState().error ?? '更新失敗')
      }
      return
    }
    const created = await createReplyPrompt({
      name: name.trim(),
      description: description.trim(),
      prompt: text,
    })
    if (created) {
      toast.success(`已儲存「${created.name}」`)
      setName('')
      setDescription('')
      setText('')
    } else {
      toast.error(useReplySettingsStore.getState().error ?? '儲存失敗')
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold">常用提示詞</h2>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          存起來重複使用的回覆要求。套用之後仍然可以在草稿頁微調——送出時以輸入框裡的內容為準。
        </p>
      </div>

      <section className="overflow-hidden rounded-xl border border-border bg-surface">
        <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2">
          <h3 className="text-sm font-semibold">{editingId === null ? '新增' : '編輯'}</h3>
          {editingId === null && customPrompt.trim() ? (
            <Button
              type="button"
              size="xs"
              variant="ghost"
              className="h-5 px-1 text-2xs text-muted-foreground"
              onClick={() => setText(customPrompt)}
            >
              帶入草稿頁目前的內容
            </Button>
          ) : null}
          {editingId !== null ? (
            <Button
              type="button"
              size="xs"
              variant="ghost"
              className="h-5 px-1 text-2xs"
              onClick={() => {
                setEditingId(null)
                setName('')
                setDescription('')
                setText('')
              }}
            >
              取消編輯
            </Button>
          ) : null}
        </div>
        <div className="space-y-2 p-4">
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <Label htmlFor="preset-name" className="text-xs text-muted-foreground">
                名稱
              </Label>
              <Input
                id="preset-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="我的工程師回覆"
                className="h-7 text-xs"
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="preset-desc" className="text-xs text-muted-foreground">
                說明（選填）
              </Label>
              <Input
                id="preset-desc"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="平常回工程團隊使用"
                className="h-7 text-xs"
              />
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="preset-text" className="text-xs text-muted-foreground">
              內容
            </Label>
            <Textarea
              id="preset-text"
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={4}
              maxLength={2000}
              className="min-h-20 text-xs"
              placeholder="回覆不要太正式。直接告訴對方目前問題在哪，如果需要他補資料，就明確列出需要哪些資料。"
            />
          </div>
          <Button
            type="button"
            size="sm"
            className="w-full"
            disabled={!name.trim() || !text.trim()}
            onClick={() => void handleSubmit()}
          >
            {editingId === null ? '儲存' : '更新'}
          </Button>
        </div>
      </section>

      <section className="overflow-hidden rounded-xl border border-border bg-surface">
        <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2">
          <h3 className="text-sm font-semibold">已儲存（{replyPrompts.length}）</h3>
        </div>
        <div className="p-4">
          {replyPrompts.length === 0 ? (
            <p className="text-xs text-muted-foreground">還沒有儲存任何提示詞。</p>
          ) : (
            <div className="max-h-56 space-y-2 overflow-y-auto">
              {replyPrompts.map((preset) => (
                <div key={preset.id} className="space-y-1 rounded-lg border border-line bg-background p-2">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium">{preset.name}</span>
                    <div className="ml-auto flex gap-1">
                      <Button
                        type="button"
                        size="xs"
                        variant="ghost"
                        className="h-5 px-1 text-2xs"
                        onClick={() => {
                          setEditingId(preset.id)
                          setName(preset.name)
                          setDescription(preset.description)
                          setText(preset.prompt)
                        }}
                      >
                        編輯
                      </Button>
                      <Button
                        type="button"
                        size="xs"
                        variant="ghost"
                        className="h-5 px-1 text-2xs text-destructive"
                        onClick={async () => {
                          if (await deleteReplyPrompt(preset.id)) {
                            toast.success(`已刪除「${preset.name}」`)
                            if (editingId === preset.id) setEditingId(null)
                          }
                        }}
                      >
                        <Trash2Icon className="size-3" />
                      </Button>
                    </div>
                  </div>
                  {preset.description ? (
                    <p className="text-2xs text-muted-foreground">{preset.description}</p>
                  ) : null}
                  <p className="whitespace-pre-wrap text-2xs leading-snug text-muted-foreground">
                    {preset.prompt}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
