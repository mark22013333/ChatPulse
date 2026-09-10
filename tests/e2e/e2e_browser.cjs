/**
 * 瀏覽器 E2E 的共用工具。
 *
 * 既有四支 `.cjs`（test_merge_reply / test_message_preview /
 * test_tab_switch_streaming / test_draft_from_summary）各自複製了一份
 * launch ＋ 認證 ＋ 記分的樣板。這個檔把那三件事收攏，新的測試直接用。
 * 既有那四支沒有改（它們現在是綠的，不值得為了去重去動），要跟進的話
 * 換掉開頭那三十行即可。
 *
 * === 認證 ===
 *
 * 兩條路，優先走**不寫資料庫**的那條：
 *
 * 1. `CHATPULSE_SESSION` 環境變數有值 → 直接塞 cookie。**零資料庫寫入**，
 *    重用一個既有的 session。取得方式（唯讀查詢，不會改動任何東西）：
 *
 *      export CHATPULSE_SESSION=$(.venv/bin/python -c "
 *      import sqlite3, sys; sys.path.insert(0, '.')
 *      from core import config as cfg
 *      con = sqlite3.connect(cfg.DB_PATH)
 *      row = con.execute(
 *          \"select token from sessions where expires_at > datetime('now') \"
 *          'order by created_at desc limit 1').fetchone()
 *      print(row[0] if row else '')")
 *
 * 2. 沒有那個變數 → 點畫面上的「匯入既有憑證」，與既有四支測試一樣。
 *    **這條會在 sessions 表 INSERT 一筆**，跑之前要知道自己在動正式資料庫。
 *
 * 第 1 條之所以是預設，除了不寫資料庫，還因為它快得多（省掉一次匯入往返）
 * 而且不依賴登入頁的按鈕文字。
 */

const path = require('path')
const { execSync } = require('child_process')

const globalRoot = execSync('npm root -g').toString().trim()
const { chromium } = require(path.join(globalRoot, 'playwright'))

const BASE = process.env.CHATPULSE_URL || 'http://127.0.0.1:8000'

/** 記分板。與既有四支的輸出格式一致。 */
function scoreboard() {
  const results = []
  return {
    results,
    check(label, ok, detail = '') {
      results.push({ label, ok, detail })
      console.log(`  ${ok ? '✓' : '✗'} ${label}${detail ? ' — ' + detail : ''}`)
    },
    /** 印出總結並回傳應該用的 exit code。 */
    finish() {
      const pass = results.filter((r) => r.ok).length
      console.log(`\n${'='.repeat(56)}\n  ${pass}/${results.length} 項通過`)
      if (pass !== results.length) {
        console.log('  未通過：')
        results.filter((r) => !r.ok).forEach((r) => console.log(`    - ${r.label} (${r.detail})`))
      }
      console.log('='.repeat(56))
      return pass === results.length ? 0 : 1
    },
  }
}

/**
 * 開一個瀏覽器並登入。回傳 `{ browser, page, errors, authMode }`。
 *
 * `errors` 會收集 pageerror 與 console.error——收工前斷言它是空的，
 * 那條比任何個別檢查都容易抓到「改壞了但畫面看起來還好」。
 */
async function open() {
  const browser = await chromium.launch()
  const context = await browser.newContext()

  let authMode = 'import-button'
  const token = process.env.CHATPULSE_SESSION
  if (token) {
    await context.addCookies([
      {
        name: 'chatpulse_session',
        value: token,
        url: BASE,
        httpOnly: true,
        sameSite: 'Lax',
      },
    ])
    authMode = 'session-cookie'
  }

  const page = await context.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text())
  })

  await page.goto(BASE, { waitUntil: 'networkidle' })

  if (authMode === 'import-button') {
    const importBtn = page.getByRole('button', { name: /匯入既有憑證/ })
    if (await importBtn.count()) {
      await importBtn.click()
      await page.waitForTimeout(2500)
    }
  }

  return { browser, page, errors, authMode }
}

/** 換 hash 並等 app 收斂。app 的 router 監聽 hashchange，沒有整頁重載。 */
async function goHash(page, hash) {
  await page.evaluate((h) => {
    window.location.hash = h
  }, hash)
  await page.waitForTimeout(350)
}

/** 目前的 hash。 */
function hashOf(page) {
  return page.evaluate(() => window.location.hash)
}

/**
 * 只在**沒有 `inert` 的那個 pane** 裡找元素。
 *
 * 兩個工作台常駐掛載、設定是覆蓋層，所以 `page.locator(...)` 會同時選到
 * 背景那一份。改版期間就因此誤判過一次釘選功能壞掉，實際上是選到了背景的
 * SummaryHistory。
 */
function activePane(page) {
  return page.locator('div:not([inert])')
}

module.exports = { BASE, chromium, scoreboard, open, goHash, hashOf, activePane }
