import { useStreamAnnouncer } from '@/hooks/useStreamAnnouncer'

/**
 * 串流的狀態層宣告（設計規格 §10.6）。
 *
 * **這兩個容器一定要常駐**：live region 必須在內容寫進去**之前**就存在於
 * 無障礙樹裡，否則多數螢幕閱讀器不會念——「有訊息才渲染」是這個機制最常見
 * 的壞法。所以這裡永遠掛著，只是內容多半是空字串。
 *
 * 內容層（`Markdown`）刻意**沒有** aria-live，只有 aria-busy：每個 chunk 都
 * 重寫 innerHTML，設了 aria-live 等於整段重念。
 *
 * 獨立成檔還有一個效能上的理由：宣告字串每次轉換都會變，掛在 `AppShell` 裡
 * 等於讓整個 shell 跟著重繪。這裡只有這兩行 sr-only 文字會更新。
 */
export function StreamLiveRegions() {
  const { polite, alert } = useStreamAnnouncer()

  return (
    <>
      <div role="status" aria-live="polite" className="sr-only">
        {polite}
      </div>
      {/* 錯誤走 role="alert"（隱含 assertive，會打斷）——只有它值得打斷 */}
      <div role="alert" className="sr-only">
        {alert}
      </div>
    </>
  )
}
