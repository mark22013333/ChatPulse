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
 * ⚠ 這裡是**唯一**允許的 `title=`：ConfirmDialog 的 `title` 是「對話框標題」
 * 這個 React prop，不是 DOM 的 tooltip 屬性。
 *
 * 用「檔案 → 允許次數」而不是行號，是因為行號會隨每次編輯漂移、維護不了；
 * 用次數則擋得住「偷偷多加一個」。新增任何一筆都要在 review 說明為什麼
 * 不能改成可見文字。
 *
 * **2026-09-10：§10.3 的 29 處已經清完了**，這張表現在只剩 ConfirmDialog 的
 * 兩個呼叫端。29 → 2 的過程中，每一處的處置都照 §10.3 的規則走：
 * 決策必需 → 可見文字；補充細節 → `<details>`；與可見文字重複 → 刪。
 * 圖示按鈕改成把說明寫進 `aria-label`（鍵盤與觸控使用者拿得到）。
 *
 * 這張表**只能再減、不能增**。要加任何一筆，先問「為什麼這句話不能是
 * 畫面上讀得到的字」——29 處清下來，沒有一處的答案是「不能」。
 */
const TITLE_BUDGET: Record<string, number> = {
  // 兩處都是 ConfirmDialog 的 title prop（對話框標題，不是 tooltip）。
  // 它渲染成 DialogTitle 的文字節點，DOM 上不會出現 title 屬性——
  // DraftReplyWorkspace.test.tsx 與 EvidenceList.test.tsx 各有一條
  // 「渲染結果裡 [title] 選得到 0 個」在守著這件事。
  'components/summary/PublishConfirm.tsx': 1,
  'components/draft/SendReplyConfirm.tsx': 1,
}

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

  it('③ title= 只出現在白名單，且數量不得增加', () => {
    const counts: Record<string, number> = {}
    for (const file of TSX) {
      // 要先洗掉註解，與 ①②⑤ 一致（那正是 stripComments 存在的理由）。
      // 少了這一步，元件測試裡「解釋為什麼不准用 title」的註解本身會被
      // 算成違規——2026-09-10 實際踩到，EvidenceList.test.tsx 被判 2 個。
      // 註解裡的寫法不會被瀏覽器套用，所以洗掉不會放過任何真的違規。
      const n = (stripComments(readFileSync(file, 'utf8')).match(/\stitle=/g) ?? []).length
      if (n > 0) counts[relative(SRC, file)] = n
    }
    for (const [file, n] of Object.entries(counts)) {
      const budget = TITLE_BUDGET[file] ?? 0
      expect(
        n,
        `${file} 有 ${n} 個 title=，預算是 ${budget}。決策必需的資訊要改成畫面上讀得到的文字，不要放進 title。`,
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
