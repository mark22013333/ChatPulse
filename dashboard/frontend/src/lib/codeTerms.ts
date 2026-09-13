/**
 * 手動指定的程式碼檢索關鍵字（ADR-0006 的逃生門）。
 *
 * 後端的自動抽詞是**刻意做笨**的啟發式：只抓 `snake_case`／`camelCase` 這類
 * 散文不會出現的形狀，再扣掉會命中半個 repo 的泛用詞。抽不準時會白跑一次，
 * 而補償手段就是這個覆寫——證據欄會把「實際搜了哪些詞」回顯出來，使用者
 * 一眼看得出搜錯了，改完重跑一次即可。
 *
 * 送 `code_terms` 給後端就是**完全取代**自動抽詞（`server.py` 的
 * `req.code_terms ... or extract_search_terms(...)`），不是附加。
 */

/**
 * 把輸入框那一行字切成關鍵字陣列。
 *
 * 逗號與空白都當分隔符：識別字裡不會有這兩種字元，所以使用者不管打
 * `foo, bar` 還是 `foo bar` 都對。全角逗號也吃——中文輸入法下最容易打出來
 * 的就是它，而「打了全角逗號結果整串被當成一個詞」是完全看不出來的失敗。
 *
 * 去重但**保留順序**：重複的詞對後端沒有意義（`git grep` 會搜同一次），
 * 而順序是使用者自己排的，不該被打亂。
 */
export function parseCodeTerms(raw: string): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const token of raw.split(/[\s,，、]+/)) {
    const term = token.trim()
    if (!term || seen.has(term)) continue
    seen.add(term)
    out.push(term)
  }
  return out
}
