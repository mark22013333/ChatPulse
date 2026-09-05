/**
 * 串流錯誤事件 → 使用者看得懂、而且知道下一步怎麼辦的訊息。
 *
 * 後端的 message 本身已是繁體中文，但它只描述「發生了什麼」，
 * 沒說「你現在可以做什麼」。配額類錯誤最常見的下一步就是換一個供應商，
 * 所以這裡把原始訊息包成含指示的句子，而不是原封不動丟出去。
 */

/** 供應商實作名稱 → 給人看的短名（找不到就退回原名）。 */
export function providerShortName(name: string | null | undefined): string {
  switch (name) {
    case 'claude_cli':
      return 'Claude Code CLI'
    case 'gemini':
      return 'Gemini'
    default:
      return name ?? '目前的供應商'
  }
}

/**
 * @param code    後端錯誤碼
 * @param message 後端原始訊息（繁體中文）
 * @param used    這次串流實際用的供應商（來自 meta 事件），可能還沒收到
 */
export function streamErrorMessage(
  code: string,
  message: string,
  used?: string | null,
): string {
  switch (code) {
    case 'CLAUDE_QUOTA_EXCEEDED':
      return `Claude 的用量已達上限，這次沒辦法產生。可以改選其他 AI 供應商（例如 Gemini）再試一次。（${message}）`
    case 'GEMINI_QUOTA_EXCEEDED':
      return `Gemini 的用量已達上限，這次沒辦法產生。可以改選其他 AI 供應商（例如 Claude Code CLI）再試一次。（${message}）`
    case 'CLAUDE_API_ERROR':
      return `Claude 回了非預期的內容，這次沒辦法產生。可以重試一次，或改選其他 AI 供應商。（${message}）`
    case 'INVALID_PARAMETER':
      return `${message}（若剛換過 AI 供應商，請重新選一個可用的供應商再送出）`
    default:
      return used
        ? `${message}（${code}；本次供應商：${providerShortName(used)}）`
        : `${message}（${code}）`
  }
}
