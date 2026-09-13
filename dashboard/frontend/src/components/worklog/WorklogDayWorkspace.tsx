/**
 * 工時草稿的單日工作區（中欄）。
 *
 * 由上而下四段：當日證據總覽 → 時數工具列 → 草稿卡清單 → 送出區。
 * 沿用 Draft Reply 的隱喻：**系統產草稿、人逐筆確認、以本人身分送出**。
 *
 * ### 階段 2a 只有骨架
 *
 * 證據蒐集（git commit ＋ Chat 發言）、時數分配、專案歸屬的候選清單與送出
 * 都在階段 2b／2c。現在只把版位佔住。
 *
 * ### 這裡不會有的東西
 *
 * **不會有「自動選好專案」的捷徑。** 系統只給候選、信心度與證據，選擇權在
 * 使用者——理由見 `docs/adr/0008-draft-worklog-over-clipboard-export.md`：
 * 工時會進月結，月結後要走 correction-request 才改得動，一個「多半正確」
 * 的自動歸屬，代價由一個月後發現錯誤的人支付。
 */
export function WorklogDayWorkspace({ date }: { date: string | null }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="px-gutter py-gutter-tight">
        <h1 className="text-lg font-semibold">工時草稿</h1>
        <p className="text-fg-dim mt-2 max-w-prose text-sm leading-relaxed">
          {date
            ? `已選擇 ${date}。單日的草稿產生還沒接上——之後這裡會從你的 git commit 與 Chat 發言整理出這天做了什麼，你輸入當天總時數，系統按證據比例分配成 0.5 的倍數，你再逐筆確認專案歸屬。`
            : '從左欄挑一天開始。之後這裡會從你的 git commit 與 Chat 發言整理出那天做了什麼，你輸入當天總時數，系統按證據比例分配成 0.5 的倍數，你再逐筆確認專案歸屬。'}
        </p>
        <p className="text-fg-subtle mt-4 max-w-prose text-xs leading-relaxed">
          還沒有登錄任何工作 repo 的話，先到設定中心加一個——沒有 repo 就沒有
          commit 可以當證據。
        </p>
      </div>
    </div>
  )
}
