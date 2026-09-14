import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * 設計 token 的守門測試（設計規格 §15.3）。
 *
 * 這條測試同時是三件事：P5 刪掉遷移橋樑的憑據、本次改版核心約束的機械化
 * 守衛、以及防止下一個 feature 破窗的機制。
 *
 * 零相依、跑在既有的 node 環境，不需要 jsdom。
 */

const SRC = join(import.meta.dirname, '..')

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) walk(full, out)
    else out.push(full)
  }
  return out
}

const ALL = walk(SRC)
const TSX = ALL.filter((f) => f.endsWith('.tsx'))
const STYLE_SOURCES = ALL.filter((f) => f.endsWith('.css'))

/** 把註解內容換成等長空白，讓「註解裡提到某個 pattern」不會被誤判成違規。 */
function stripComments(source: string): string {
  const blank = (m: string) => m.replace(/[^\n]/g, ' ')
  return source
    .replace(/\/\*[\s\S]*?\*\//g, blank) // /* 區塊 */：CSS 與 TS 共用
    .replace(/(^|[^:])\/\/[^\n]*/g, (m, head) => head + blank(m.slice(head.length)))
}

function scan(files: string[], pattern: RegExp): string[] {
  const hits: string[] = []
  for (const file of files) {
    const lines = stripComments(readFileSync(file, 'utf8')).split('\n')
    lines.forEach((line, index) => {
      const matches = line.match(pattern)
      if (matches) {
        hits.push(`${relative(SRC, file)}:${index + 1}  ${matches.join(' ')}`)
      }
    })
  }
  return hits
}

/**
 * `title=` 的預算＝該檔案裡 `<ConfirmDialog` 的出現次數。
 *
 * ⚠ `ConfirmDialog` 的 `title` 是「對話框標題」這個 **React prop**，不是 DOM
 * 的 tooltip 屬性。它渲染成 `DialogTitle` 的文字節點，DOM 上不會出現 `title`
 * 屬性——`DraftReplyWorkspace.test.tsx` 與 `EvidenceList.test.tsx` 各有一條
 * 「渲染結果裡 `[title]` 選得到 0 個」在守著這件事。
 *
 * ### 為什麼不再用手工白名單
 *
 * 在此之前這裡是一張 `檔案 → 允許次數` 的表，註明「**只能再減、不能增**」，
 * 理由是「先問為什麼這句話不能是畫面上讀得到的字」。那條理由對 tooltip
 * 完全成立（§10.3 的 29 處清下來，沒有一處的答案是「不能」），**但對
 * `ConfirmDialog` 不成立**——它的 title 本來就是畫面上讀得到的標題。
 *
 * 於是這條規則誤傷了它唯一允許的用途：2026-09-13 做工時草稿的設定頁時，
 * 刪除確認因為加不進白名單，只好繞開 `ConfirmDialog`、拿它底下那組 Dialog
 * 元件自己重組一份。DOM 一模一樣，代價是多一份會漂移的重複程式碼——
 * 而且 `ConfirmDialog` 日後改進時，那一份不會跟著改。
 *
 * 改成用 `<ConfirmDialog` 的出現次數當預算之後：合法用途自動有額度，
 * tooltip 仍然一個都不准（沒有 ConfirmDialog 的檔案預算就是 0），
 * 而且不必每加一個確認框就回來改這個檔。
 *
 * ### 這個近似的限度
 *
 * 它**不驗證** `title=` 真的寫在 `<ConfirmDialog` 標籤裡。一個檔案若同時有
 * 一個 ConfirmDialog 與一個 tooltip，數量剛好相等時會漏掉。要精確就得解析
 * JSX，代價遠高於它擋住的風險——真正的防線是上面說的那兩條 DOM 斷言。
 */
const CONFIRM_DIALOG = /<ConfirmDialog\b/g

describe('設計 token 守門', () => {
  it('① 沒有繞過 token 的具名色', () => {
    const hits = scan(
      TSX,
      /\b(?:bg|text|border|ring|from|to|via|fill|stroke|divide|outline)-(?:sky|emerald|amber|violet|rose|teal|red|green|blue|yellow|orange|lime|cyan|indigo|purple|pink|slate|gray|zinc|neutral|stone)-\d{2,3}(?:\/\d+)?\b/g,
    )
    expect(hits).toEqual([])
  })

  it('② 沒有任意字級——一律走 text-2xs / xs / sm / base / md / lg', () => {
    // 不可只比對 `\d+px`：`text-[0.8rem]` 是實際存在過的寫法，
    // 只比對 px 的 pattern 會靜默放過它然後回報「0 命中」
    const hits = scan(TSX, /text-\[[0-9.]+(?:px|rem|em)\]/g)
    expect(hits).toEqual([])
  })

  it('③ title= 只用在 ConfirmDialog 的 title prop 上', () => {
    for (const file of TSX) {
      // 要先洗掉註解，與 ①②⑤ 一致（那正是 stripComments 存在的理由）。
      // 少了這一步，元件測試裡「解釋為什麼不准用 title」的註解本身會被
      // 算成違規——2026-09-10 實際踩到，EvidenceList.test.tsx 被判 2 個。
      // 註解裡的寫法不會被瀏覽器套用，所以洗掉不會放過任何真的違規。
      const source = stripComments(readFileSync(file, 'utf8'))
      const used = (source.match(/\stitle=/g) ?? []).length
      if (used === 0) continue
      const budget = (source.match(CONFIRM_DIALOG) ?? []).length
      expect(
        used,
        `${relative(SRC, file)} 有 ${used} 個 title=，但只有 ${budget} 個 ConfirmDialog。` +
          `決策必需的資訊要改成畫面上讀得到的文字，不要放進 title。`,
      ).toBeLessThanOrEqual(budget)
    }
  })

  it('④ 不再用 tooltip 承載資訊', () => {
    expect(scan(TSX, /<Tooltip/g)).toEqual([])
  })

  it('⑤ 沒有硬編碼色碼——連 index.css 也不例外', () => {
    // `#0ea5e9` 曾經以 `var(--color-sky-500, #0ea5e9)` 的 fallback 形式
    // 藏在 index.css 裡四次。留著 fallback 等於允許一個永遠不會被更新的
    // 舊藍色偷偷存在。
    // scan() 會先把註解洗掉，所以 index.css 裡標注對比比值用的 `/* #191b1d 16.58:1 */`
    // 不會被誤判——那些是文件，不是會被瀏覽器套用的值
    expect(scan([...TSX, ...STYLE_SOURCES], /#[0-9a-fA-F]{3,8}\b/g)).toEqual([])
  })
})

describe('守門測試自己有效嗎（正對照）', () => {
  // 回報「0 命中」的把關工具必須先證明它抓得到已知的違規，
  // 否則 pattern 寫錯時會靜默通過（實例：`\d+px` 漏掉 `text-[0.8rem]`）
  const FIXTURE = [
    '<div className="bg-sky-500 text-[10px]" title="x">',
    '<span className="text-[0.8rem]">',
    'color: #0ea5e9;',
  ].join('\n')

  it('具名色的 pattern 抓得到 bg-sky-500', () => {
    expect(
      FIXTURE.match(
        /\b(?:bg|text|border)-(?:sky|emerald|amber|violet)-\d{2,3}(?:\/\d+)?\b/g,
      ),
    ).toContain('bg-sky-500')
  })

  it('字級的 pattern 同時抓得到 px 與 rem 兩種寫法', () => {
    const hits = FIXTURE.match(/text-\[[0-9.]+(?:px|rem|em)\]/g) ?? []
    expect(hits).toContain('text-[10px]')
    expect(hits).toContain('text-[0.8rem]')
  })

  it('色碼的 pattern 抓得到 #0ea5e9', () => {
    expect(FIXTURE.match(/#[0-9a-fA-F]{3,8}\b/g)).toContain('#0ea5e9')
  })

  it('**title= 的 pattern 抓得到真的屬性、但不抓註解裡提到的**', () => {
    // ③ 改成先洗註解之後，這條就是它的正對照：證明洗掉的只有註解，
    // 真正寫在 JSX 上的 title= 一個都沒放過。
    expect(stripComments(FIXTURE).match(/\stitle=/g)).toHaveLength(1)

    const inComment = ['// 這行在講 title= 這個屬性', '/* 這裡也提到 title="x" */'].join('\n')
    expect(stripComments(inComment).match(/\stitle=/g)).toBeNull()
  })
})
