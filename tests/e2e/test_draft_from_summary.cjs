/**
 * E2E：從摘要工作台對任何對話（含私訊）產生回覆草稿。
 *
 * 對應的需求：使用者的私訊不會出現在 Mention 收件匣（沒人會在私訊裡 @ 你），
 * 所以原本完全無法對私訊產草稿。作法是按下按鈕時請後端挑出「對方最後說的
 * 那則訊息」並合成一個 state='manual' 的草稿目標，再走原本那條草稿串流。
 *
 * 這支測試要守住四件事：
 *   1. 按鈕存在且按得下去
 *   2. 按下後真的切到草稿工作區，而且已經在串流
 *   3. 草稿目標**不會**污染 Mention 收件匣（那是待辦清單，不該混入手動項目）
 *   4. 收件匣的未讀數不受影響
 *
 * 串流一樣在瀏覽器裡假造（理由見 test_tab_switch_streaming.cjs）：時序可控、
 * 不燒 AI 額度。但 draft-target 端點走真實後端——那正是這次要驗的東西，
 * 它會真的讀 Google Chat 的訊息並寫入一筆 mention。
 *
 * 前置條件：服務已在 127.0.0.1:8000 執行，資料庫裡有授權過的 Viewer。
 *
 * 用法：node tests/e2e/test_draft_from_summary.cjs [--headed]
 */

const path = require('path')
const { execSync } = require('child_process')
const globalRoot = execSync('npm root -g').toString().trim()
const { chromium } = require(path.join(globalRoot, 'playwright'))

const BASE = process.env.CHATPULSE_URL || 'http://127.0.0.1:8000'
const HEADED = process.argv.includes('--headed')

const results = []
function check(name, passed, detail = '') {
  results.push({ name, passed, detail })
  console.log(`  ${passed ? '✓' : '✗'} ${name}${detail ? ` — ${detail}` : ''}`)
}

/** 只攔草稿串流；draft-target 與其他請求都走真實後端。 */
function installFakeDraftStream() {
  const origFetch = window.fetch
  window.__sse = { started: false, controller: null, aborted: false }
  window.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : input?.url ?? ''
    if (!url.includes('/draft/stream')) return origFetch(input, init)
    window.__sse.started = true
    init?.signal?.addEventListener('abort', () => {
      window.__sse.aborted = true
    })
    const stream = new ReadableStream({
      start(c) {
        window.__sse.controller = c
      },
    })
    return new Response(stream, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    })
  }
  window.__push = (obj) =>
    window.__sse.controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(obj)}\n\n`))
}

;(async () => {
  const browser = await chromium.launch({ headless: !HEADED })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const consoleErrors = []
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text())
  })

  try {
    await page.addInitScript(installFakeDraftStream)
    await page.goto(BASE, { waitUntil: 'networkidle' })

    const importBtn = page.getByRole('button', { name: '匯入既有憑證' })
    if (await importBtn.isVisible().catch(() => false)) await importBtn.click()
    await page.locator('nav button', { hasText: '摘要工作台' }).waitFor({ timeout: 20000 })
    await page.waitForTimeout(2000)

    // 記下收件匣現在的未讀數，最後要確認沒被影響
    const badgeBefore = (
      await page.locator('nav button', { hasText: 'Mention 收件匣' }).innerText()
    ).replace(/\D/g, '')
    console.log(`\n【1】起始狀態（收件匣未讀 ${badgeBefore || 0}）`)
    check('儀表板載入', true)

    // 選一個私訊——那正是原本產不了草稿的情境
    console.log('\n【2】選一個私訊')
    const dm = page
      .locator('aside')
      .first()
      .locator('button')
      .filter({ hasText: '私訊 ·' })
      .first()
    check('清單裡有私訊', (await dm.count()) > 0)
    await dm.click()
    await page.waitForTimeout(800)

    console.log('\n【3】按「產生回覆草稿」')
    const draftBtn = page.getByRole('button', { name: '產生回覆草稿' })
    await draftBtn.waitFor({ timeout: 10000 })
    check('按鈕存在', true)
    await draftBtn.click()

    // 這一步會打真實後端：讀訊息、挑出對方最後說的那則、寫入草稿目標
    await page.waitForFunction(() => window.__sse?.started === true, { timeout: 30000 })
    check('草稿目標建立成功，且已開始串流', true)

    console.log('\n【4】應該已經切到草稿工作區')
    await page.waitForTimeout(600)
    const activeTab = await page.locator('nav button.bg-background').innerText()
    check('已切到 Mention 收件匣頁籤', activeTab.includes('Mention'), `目前在「${activeTab.trim()}」`)

    await page.evaluate(() => window.__push({ type: 'meta', provider: 'fake', model: 'fake' }))
    for (const n of [1, 2, 3]) {
      await page.evaluate((i) => window.__push({ type: 'chunk', text: `草稿第${i}段。` }), n)
      await page.waitForTimeout(120)
    }
    await page.waitForTimeout(500)
    const mainText = await page.locator('main').innerText()
    check('草稿內容顯示在畫面上', mainText.includes('草稿第3段。'))

    console.log('\n【5】收件匣不該被污染')
    const badgeAfter = (
      await page.locator('nav button', { hasText: 'Mention 收件匣' }).innerText()
    ).replace(/\D/g, '')
    check('未讀數沒變', badgeBefore === badgeAfter, `${badgeBefore || 0} -> ${badgeAfter || 0}`)

    // 直接問後端：收件匣清單裡不該出現 state=manual 的項目
    const inbox = await page.evaluate(async () => {
      const r = await fetch('/api/v1/mentions?with_content=false', { credentials: 'same-origin' })
      return r.json()
    })
    const manualInInbox = (inbox.mentions || []).filter((m) => m.state === 'manual').length
    check('收件匣清單沒有 manual 項目', manualInInbox === 0, `找到 ${manualInInbox} 筆`)

    await page.evaluate(() => window.__push({ type: 'done', draft_id: null }))
    await page.waitForTimeout(400)
    check('沒有 console error', consoleErrors.length === 0, consoleErrors[0]?.slice(0, 80) || '')
  } catch (err) {
    check('測試執行完成', false, String(err).split('\n')[0].slice(0, 140))
    await page
      .screenshot({ path: path.join(__dirname, 'reports', 'draft-from-summary-failure.png') })
      .catch(() => {})
  } finally {
    await browser.close()
  }

  const failed = results.filter((r) => !r.passed)
  console.log('\n' + '='.repeat(56))
  console.log(`  ${results.length - failed.length}/${results.length} 項通過`)
  failed.forEach((f) => console.log(`    - ${f.name}${f.detail ? `（${f.detail}）` : ''}`))
  console.log('='.repeat(56))
  process.exit(failed.length ? 1 : 0)
})()
