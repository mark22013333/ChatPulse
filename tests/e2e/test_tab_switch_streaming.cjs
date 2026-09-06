/**
 * E2E：切換頁籤不該中斷 AI 串流，也不該清空已生成的內容。
 *
 * 對應的 bug：使用者回報「產生草稿時切到別的頁籤，AI 思考的介面就不見了」。
 * 查出來是三層問題疊加（詳見 git log 的 fix(dashboard) 那筆）：
 *   1. 元件卸載時呼叫 abort()，串流**真的被中止**，白燒一次 AI 額度
 *   2. 元件掛載時無條件 reset()，就算不中止也看不到內容
 *   3. 後端整段跑完才落盤，中斷即全部丟失
 *
 * **為什麼在瀏覽器裡假造串流，而不是真的叫一次 AI**：
 *   - 時序要可控。這個 bug 只有在「生成進行中」切頁籤才會出現，真實 AI
 *     回多快不由我們決定，測試會變成看運氣。
 *   - 不燒 AI 額度，也不會在使用者的資料庫留下測試資料。
 *   - 最重要的是能直接觀察 `AbortSignal` 有沒有被觸發——那正是 bug 的核心，
 *     從畫面上反而看不出來（畫面空白可能是中止，也可能只是被清空）。
 * 後端「中斷後補存」那一層無法用這個方式驗，它有自己的 Python 測試。
 *
 * 前置條件：服務已在 127.0.0.1:8000 執行，且資料庫裡已有一位授權過的 Viewer
 * （登入畫面的「匯入既有憑證」按得下去）。
 *
 * 用法：
 *   node tests/e2e/test_tab_switch_streaming.cjs
 *   node tests/e2e/test_tab_switch_streaming.cjs --headed   # 想親眼看它跑
 */

const path = require('path')
const { execSync } = require('child_process')

// playwright 裝在全域（npm i -g playwright），這裡自己解析位置，
// 不依賴呼叫端設好 NODE_PATH。
const globalRoot = execSync('npm root -g').toString().trim()
const { chromium } = require(path.join(globalRoot, 'playwright'))

const BASE = process.env.CHATPULSE_URL || 'http://127.0.0.1:8000'
const HEADED = process.argv.includes('--headed')

const results = []
function check(name, passed, detail = '') {
  results.push({ name, passed, detail })
  console.log(`  ${passed ? '✓' : '✗'} ${name}${detail ? ` — ${detail}` : ''}`)
}

/**
 * 在頁面裡把 fetch 換掉：對摘要串流端點回傳一個我們自己控制的 ReadableStream。
 * 掛在 window 上的三個把手讓測試可以精確決定「什麼時候送下一段」。
 */
function installFakeStream() {
  const origFetch = window.fetch
  window.__sse = { aborted: false, closed: false, started: false, controller: null }

  window.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : input?.url ?? ''
    // 端點是 /api/v1/summarize/stream（api.ts 的 streamUrls.summarize）。
    // 其餘請求一律放行走真實後端。
    if (!url.includes('/summarize/stream')) return origFetch(input, init)

    window.__sse.started = true
    // 這是整個測試最重要的一行：如果前端在切頁籤時 abort()，這裡會被觸發。
    // 修好之後它應該從頭到尾都是 false。
    init?.signal?.addEventListener('abort', () => {
      window.__sse.aborted = true
    })

    const stream = new ReadableStream({
      start(controller) {
        window.__sse.controller = controller
      },
    })
    return new Response(stream, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    })
  }

  window.__push = (obj) => {
    const enc = new TextEncoder()
    window.__sse.controller.enqueue(enc.encode(`data: ${JSON.stringify(obj)}\n\n`))
  }
  window.__close = () => {
    // 前端收到 done 之後會照設計 cancel() 掉 reader（見 lib/sse.ts），
    // 此時 controller 已經是 closed/errored，再 close 一次會拋。
    // 那是正常行為，不是產品問題，所以這裡吞掉。
    try {
      window.__sse.controller.close()
    } catch {
      /* 已經被前端關掉了 */
    }
    window.__sse.closed = true
  }
}

;(async () => {
  const browser = await chromium.launch({ headless: !HEADED })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const consoleErrors = []
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text())
  })

  try {
    await page.addInitScript(installFakeStream)
    await page.goto(BASE, { waitUntil: 'networkidle' })

    // ── 登入 ─────────────────────────────────────────────
    const importBtn = page.getByRole('button', { name: '匯入既有憑證' })
    if (await importBtn.isVisible().catch(() => false)) {
      await importBtn.click()
    }
    await page.locator('nav button', { hasText: '摘要工作台' }).waitFor({ timeout: 20000 })
    console.log('\n【1】登入並進入儀表板')
    check('儀表板載入', true)

    // ── 選一個 Space 並開始摘要 ──────────────────────────
    console.log('\n【2】開始摘要（串流由測試控制，不呼叫真實 AI）')
    // 左欄第一個 aside 是 Space 清單，但它開頭有「強制刷新」，要濾掉才選得到 Space
    await page
      .locator('aside')
      .first()
      .locator('button')
      .filter({ hasNotText: '強制刷新' })
      .first()
      .click()

    // 沒選 Space 之前「開始摘要」是 disabled，等它變成可按再點
    await page.waitForFunction(
      () => {
        const b = [...document.querySelectorAll('main button')].find(
          (x) => x.textContent.trim() === '開始摘要',
        )
        return b && !b.disabled
      },
      { timeout: 15000 },
    )
    check('選定 Space 後「開始摘要」可按', true)
    await page.getByRole('button', { name: '開始摘要' }).click()

    await page.waitForFunction(() => window.__sse?.started === true, { timeout: 15000 })
    check('串流請求已送出', true)

    // 先送 meta，再送前三段內容
    await page.evaluate(() => window.__push({ type: 'meta', provider: 'fake', model: 'fake-model' }))
    for (const i of [1, 2, 3]) {
      await page.evaluate((n) => window.__push({ type: 'chunk', text: `切走前第${n}段。` }), i)
      await page.waitForTimeout(120)
    }
    await page.waitForTimeout(400)

    const beforeText = await page.locator('main').innerText()
    check('切走前畫面已出現內容', beforeText.includes('切走前第3段。'),
      `找到 ${(beforeText.match(/切走前第\d段。/g) || []).length} 段`)

    // ── 切到另一個頁籤 ───────────────────────────────────
    console.log('\n【3】切換到 Mention 收件匣（bug 就發生在這裡）')
    await page.locator('nav button', { hasText: 'Mention 收件匣' }).click()
    await page.waitForTimeout(600)

    const abortedAfterSwitch = await page.evaluate(() => window.__sse.aborted)
    check('切走後串流「沒有」被中止', abortedAfterSwitch === false,
      abortedAfterSwitch ? '串流被 abort()，生成中斷、額度白燒' : 'AbortSignal 未觸發')

    // 頁籤上的生成中指示
    const busyDot = page.locator('nav button', { hasText: '摘要工作台' }).locator('span.rounded-full')
    check('摘要頁籤顯示「生成中」指示', (await busyDot.count()) > 0)

    // ── 切走期間繼續送內容 ───────────────────────────────
    console.log('\n【4】切走期間串流繼續（驗證後端仍在寫入前端 store）')
    for (const i of [4, 5]) {
      await page.evaluate((n) => window.__push({ type: 'chunk', text: `切走後第${n}段。` }), i)
      await page.waitForTimeout(150)
    }

    // ── 切回來 ───────────────────────────────────────────
    console.log('\n【5】切回摘要工作台')
    await page.locator('nav button', { hasText: '摘要工作台' }).click()
    await page.waitForTimeout(700)

    const afterText = await page.locator('main').innerText()
    check('切回來後「切走前」的內容還在', afterText.includes('切走前第3段。'),
      afterText.includes('切走前第3段。') ? '' : '內容被 reset() 清空了')
    check('切走期間送出的內容也收到了', afterText.includes('切走後第5段。'),
      afterText.includes('切走後第5段。') ? '' : '串流在切走時就斷了')

    // ── 收尾：讓串流正常結束 ─────────────────────────────
    console.log('\n【6】串流正常結束')
    await page.evaluate(() => window.__push({ type: 'done', summary_id: null }))
    await page.evaluate(() => window.__close())
    await page.waitForTimeout(600)

    const stillAborted = await page.evaluate(() => window.__sse.aborted)
    check('整個過程都沒有被 abort', stillAborted === false)

    const finalText = await page.locator('main').innerText()
    check('五段內容全數保留', [1, 2, 3].every((n) => finalText.includes(`切走前第${n}段。`))
      && [4, 5].every((n) => finalText.includes(`切走後第${n}段。`)))

    check('沒有 console error', consoleErrors.length === 0,
      consoleErrors.length ? consoleErrors[0].slice(0, 80) : '')
  } catch (err) {
    check('測試執行完成', false, String(err).split('\n')[0].slice(0, 120))
    await page.screenshot({ path: path.join(__dirname, 'reports', 'tab-switch-failure.png') })
      .catch(() => {})
  } finally {
    await browser.close()
  }

  const failed = results.filter((r) => !r.passed)
  console.log('\n' + '='.repeat(56))
  console.log(`  ${results.length - failed.length}/${results.length} 項通過`)
  if (failed.length) {
    console.log('  未通過：')
    failed.forEach((f) => console.log(`    - ${f.name}${f.detail ? `（${f.detail}）` : ''}`))
  }
  console.log('='.repeat(56))
  process.exit(failed.length ? 1 : 0)
})()
