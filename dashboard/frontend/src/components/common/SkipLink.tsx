interface SkipLinkProps {
  /** 要跳到哪個元素的 id（含 `#`）。 */
  href: string
}

/**
 * 跳到主要內容（規格 §9.2、§10.1）。
 *
 * 左欄的虛擬清單有 436 筆，鍵盤使用者要 Tab 很久才到得了主要內容。
 * 平常是 `sr-only`，取到焦點才浮出來。
 *
 * `href` 由呼叫端決定，因為「主要內容」會變：768–1024 的主從切換在顯示清單
 * 那一半時 `<main>` 是 inert 的，跳過去等於跳到一個不存在的地方。
 */
export function SkipLink({ href }: SkipLinkProps) {
  return (
    <a
      href={href}
      className="sr-only focus:not-sr-only focus:bg-raised focus:text-foreground focus:shadow-overlay focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:px-3 focus:py-2 focus:text-sm"
    >
      跳到主要內容
    </a>
  )
}
