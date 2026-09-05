export interface ActionItem {
  /** 穩定 key：以「序號 + 原文」組成，串流過程重繪不會錯位 */
  key: string
  /** 中括號內的負責人或組別；沒有標注時為 null */
  owner: string | null
  /** 任務本文 */
  task: string
  /** 原始行（去掉項目符號後） */
  raw: string
}

/** `• [負責人或組別] 任務` —— 規格 5.3／契約前端注意事項 4 指定的格式。 */
const OWNED_ITEM = /^\s*[•*+-]\s*[[［【]\s*([^\]］】]+?)\s*[\]］】]\s*(.+?)\s*$/
/** 一般項目符號行（退回既有 index.html 的做法時使用） */
const PLAIN_ITEM = /^\s*[•*+-]\s+(.+?)\s*$/
const HEADING = /^\s*#{1,6}\s+/
const ACTION_HEADING = /(待辦|行動項|Action\s*Items?)/i

/**
 * 從摘要 Markdown 萃取 Action Items。
 *
 * 主策略：全文掃描 `• [負責人] 任務` 格式的行（規格明訂的格式）。
 * 退路：若一條都沒抓到，改用既有 index.html 的做法——找「待辦事項／Action Items」
 *       標題之後、下一個標題之前的項目符號行。
 */
export function extractActionItems(markdown: string): ActionItem[] {
  if (!markdown) return []
  const lines = markdown.split('\n')

  const owned: ActionItem[] = []
  lines.forEach((line, index) => {
    const match = OWNED_ITEM.exec(line)
    if (!match) return
    const owner = match[1].trim()
    const task = stripInlineMarkdown(match[2])
    if (!task) return
    owned.push({ key: `${index}:${owner}:${task}`, owner, task, raw: `[${owner}] ${task}` })
  })
  if (owned.length > 0) return owned

  const fallback: ActionItem[] = []
  let inActionSection = false
  lines.forEach((line, index) => {
    if (HEADING.test(line)) {
      inActionSection = ACTION_HEADING.test(line)
      return
    }
    if (!inActionSection) return
    const match = PLAIN_ITEM.exec(line)
    if (!match) return
    const task = stripInlineMarkdown(match[1])
    if (!task) return
    fallback.push({ key: `${index}:${task}`, owner: null, task, raw: task })
  })
  return fallback
}

/** 去掉粗體／行內程式碼等標記，讓 checkbox 文字乾淨。 */
function stripInlineMarkdown(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/__(.+?)__/g, '$1')
    .replace(/`(.+?)`/g, '$1')
    .replace(/^\[\s*[x ]\s*\]\s*/i, '')
    .trim()
}

/** 轉成 Markdown 待辦清單，供「複製為 Markdown」使用。 */
export function actionItemsToMarkdown(items: ActionItem[], checked: Set<string>): string {
  return items
    .map((item) => {
      const box = checked.has(item.key) ? '[x]' : '[ ]'
      const owner = item.owner ? `**${item.owner}**：` : ''
      return `- ${box} ${owner}${item.task}`
    })
    .join('\n')
}
