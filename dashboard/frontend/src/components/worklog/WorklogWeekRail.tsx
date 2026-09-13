/**
 * 工時草稿的週欄（左欄）。
 *
 * 這一欄回答「這一週哪幾天還沒交代」。七天各一列，狀態用字符與規線表達
 * （`✓` 已送出／`▸` 有草稿未送／`—` 未產生／`†` 部分失敗），小時數靠右
 * 共用同一條右緣——與證據欄同一套帳本式排版，不做卡片。
 *
 * ### 階段 2a 只有骨架
 *
 * 週切換、週合計（`total_hours`／`target_hours`／`completion_pct`）與七天列
 * 都要等 `GET /api/v1/worklogs/week` 接上（階段 2b）。現在先把版位佔住，
 * 讓導航、路由與證據欄那一整條路徑可以先驗證。
 */
export function WorklogWeekRail() {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-line-strong flex shrink-0 items-center gap-2 border-b px-gutter-tight py-2.5">
        <h2 className="text-xs font-semibold tracking-wide">本週</h2>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <p className="text-fg-dim px-gutter-tight py-6 text-xs leading-relaxed">
          週檢視還沒接上。這裡之後會列出一週七天各自的填報狀況——哪幾天已經送出、
          哪幾天有草稿還沒送、本週合計與目標時數。
        </p>
      </div>
    </div>
  )
}
