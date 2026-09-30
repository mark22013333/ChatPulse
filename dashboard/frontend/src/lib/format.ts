/** 相對時間：3 小時前、2 天前。無法解析時回傳 '—'。 */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const diffSeconds = Math.round((Date.now() - then) / 1000)
  const future = diffSeconds < 0
  const seconds = Math.abs(diffSeconds)

  const units: Array<[number, string]> = [
    [60, '秒'],
    [3600, '分鐘'],
    [86400, '小時'],
    [2592000, '天'],
    [31536000, '個月'],
  ]

  if (seconds < 45) return future ? '即將' : '剛剛'
  for (let i = 0; i < units.length; i += 1) {
    const [limit, label] = units[i]
    if (seconds < limit) {
      const divisor = i === 0 ? 1 : units[i - 1][0]
      const amount = Math.floor(seconds / divisor)
      return future ? `${amount} ${label}後` : `${amount} ${label}前`
    }
  }
  const years = Math.floor(seconds / 31536000)
  return future ? `${years} 年後` : `${years} 年前`
}

/** 絕對時間：2026-09-05 01:23（本地時區）。 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 千分位。 */
export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString('zh-TW')
}

/**
 * ISO 星期 → 中文。
 *
 * **一定要用對照表，不可以拿 `weekday` 去索引一個七元素陣列。** 後端回的是
 * ISO 星期（1＝一），但那個約定哪天變了（或者有人把它換成 ZPlanner 原生的
 * 英文縮寫），陣列索引會安靜地整排錯位——七列每一列都標錯星期，而日期是對的，
 * 光看畫面完全不會發現。對照表查不到就是查不到，出不了這種錯。
 *
 * （原本住在已移除的 `WeekDayRow.tsx`，現由 `WorklogTable` 使用。）
 */
const WEEKDAY_LABEL: Record<number, string> = {
  1: '週一',
  2: '週二',
  3: '週三',
  4: '週四',
  5: '週五',
  6: '週六',
  7: '週日',
}

/** 查不到就回空字串：寧可只顯示日期，也不要編一個星期出來。 */
export function weekdayLabel(weekday: number): string {
  return WEEKDAY_LABEL[weekday] ?? ''
}

/**
 * `2026-09-07` → `09/07`。
 *
 * 純字串切割，**不經過 `Date`**：`new Date('2026-09-07')` 會被當成 UTC 午夜，
 * 在 UTC+8 以外的時區印回來就變成前一天。
 */
export function formatMonthDay(iso: string): string {
  const parts = iso.split('-')
  return parts.length === 3 ? `${parts[1]}/${parts[2]}` : iso
}

/** Space 類型的中文標籤。 */
export function spaceTypeLabel(type: string | null | undefined): string {
  switch (type) {
    case 'SPACE':
      return '群組'
    case 'DIRECT_MESSAGE':
    case 'DM':
      return '私訊'
    case 'GROUP_CHAT':
      return '多人對話'
    default:
      return type || '未知'
  }
}
