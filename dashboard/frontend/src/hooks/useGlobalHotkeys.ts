import { useEffect } from 'react'
import { isTypingTarget, resolveHotkey } from '@/lib/hotkeys'
import { isComposing } from '@/lib/keyboard'
import { useUiStore } from '@/store/ui'

interface Handlers {
  onToggleEvidence: () => void
}

/**
 * 全域快捷鍵（設計規格 §11）。
 *
 * 三條規則：
 * 1. **輸入法組字中一律不理**——注音選字按 Enter 是「選這個字」
 * 2. 命令面板開著時整組停用，交給面板自己處理
 * 3. 單鍵快捷鍵在輸入框裡不生效（`⌘K`／`Esc` 例外）
 */
export function useGlobalHotkeys({ onToggleEvidence }: Handlers) {
  const paletteOpen = useUiStore((state) => state.paletteOpen)
  const setPaletteOpen = useUiStore((state) => state.setPaletteOpen)

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (isComposing(event)) return
      // 面板開著時它自己接管鍵盤，這裡完全不動作
      if (paletteOpen) return

      const action = resolveHotkey(event, isTypingTarget(event.target))
      if (!action) return

      if (action === 'palette') {
        // Firefox 把 ⌘K／Ctrl+K 綁在搜尋列上，不 preventDefault 會被搶走
        event.preventDefault()
        setPaletteOpen(true)
        return
      }

      if (action === 'toggle-evidence') {
        event.preventDefault()
        onToggleEvidence()
        return
      }

      if (action === 'focus-search') {
        const search = document.querySelector<HTMLInputElement>(
          'aside input[type="text"], aside input:not([type])',
        )
        if (search) {
          event.preventDefault()
          search.focus()
          search.select()
        }
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [paletteOpen, setPaletteOpen, onToggleEvidence])
}
