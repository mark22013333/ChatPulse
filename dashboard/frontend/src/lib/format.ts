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
