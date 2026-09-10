import { CodeProjectSettings } from '@/components/CodeProjectSettings'

/**
 * 參考專案設定頁（ADR-0006）。
 *
 * 在此之前這一塊塞在草稿工作區的右欄裡，1024px 以下整個消失。現在它是設定
 * 中心的一頁；工作區只留「這一次要勾哪幾個環境」的快速切換。
 *
 * 這一層目前是薄殼——`CodeProjectSettings` 本身 246 行、還在門檻內，先原地
 * 沿用，把它從右欄搬出來才是這個 Phase 的重點。要再拆成清單頁與表單，等
 * 建檔前 dry-run 驗證（`POST /code-projects/verify`）接上時一起做。
 */
export function CodeProjectsPage() {
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-md font-semibold">參考專案</h2>
        <p className="text-fg-dim mt-1 text-xs leading-relaxed">
          登錄這台機器上的程式碼資料夾，Draft Reply 就能引用實際程式碼回答問題。
          每個環境對應哪個分支要在這裡講清楚——拿 UAT 的程式碼回答正式環境的問題，
          會產生看起來有憑有據、實際上錯的答案。
        </p>
      </div>
      <CodeProjectSettings />
    </div>
  )
}
