/**
 * E2E：收件匣多選、合併成一則回話。
 *
 * 使用者回報的問題：同一個人在同一個私訊裡連問兩件事，會變成收件匣裡的兩筆，
 * 而每筆各自產生一份草稿——脈絡其實兩邊都看得到（flat_window 修好之後），
 * 但沒辦法「一起回成一則」，只能分兩次送出。
 *
 * 這支測試要守住五件事：
 *   1. 待處理清單有 checkbox，勾了會出現合併列
 *   2. 只勾一則時不讓你按「合併」（那是單則回覆，不需要合併）
 *   3. **不同聊天室的項目要變成不可勾，而且畫面上說得出原因**——
 *      只變灰的話使用者只會覺得壞了
 *   4. 按下合併產生後，request 真的帶了 merge_mention_ids
 *   5. 草稿工作區顯示「合併回覆 N 則」——送出會一次結掉這 N 則，
 *      這件事必須在按送出之前就看得到
 *
 * 串流在瀏覽器裡假造（同 test_tab_switch_streaming.cjs）：時序可控、不燒 AI 額度。
 * draft-target 與收件匣清單走真實後端。
 *
 * 前置條件：服務已執行且資料庫裡有授權過的 Viewer，
 * 並且**同一個 Space 至少有兩筆待處理**（沒有的話請先從摘要工作台
 * 對同一個私訊按兩次「產生回覆草稿」，中間對方要有新訊息）。
 *
 * 用法：node tests/e2e/test_merge_reply.cjs
 */

const path = require('path')
const { execSync } = require('child_process')
const globalRoot = execSync('npm root -g').toString().trim()
const { chromium } = require(path.join(globalRoot, 'playwright'))

const BASE = process.env.CHATPULSE_URL || 'http://127.0.0.1:8000'

const results = []
const check = (label, ok, detail = '') => {
  results.push({ label, ok, detail })
  console.log(`  ${ok ? '✓' : '✗'} ${label}${detail ? ' — ' + detail : ''}`)
}

;(async () => {
  const b = await chromium.launch()
  const page = await b.newPage()
  const errs = []
  page.on('pageerror', (e) => errs.push(e.message))
  page.on('console', (m) => {
    if (m.type() === 'error') errs.push(m.text())
  })

  let draftBody = null
  let draftUrl = null
  await page.route('**/draft/stream', async (route) => {
    draftBody = JSON.parse(route.request().postData() || '{}')
    draftUrl = route.request().url()
    // 回一個假的 meta，不打真的 AI
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body:
        'data: ' +
        JSON.stringify({
          type: 'meta',
          mention_id: draftBody.__id,
          context: { mode: 'flat_window', message_count: 8, anchor_count: 2, coverage: 'full', time_range: { start: '', end: '' }, blocks: [] },
          answering: [{ mention_id: 45 }, { mention_id: 46 }],
        }) +
        '\n\n' +
        'data: ' + JSON.stringify({ type: 'chunk', text: '### ✍️ 建議回話\n測試' }) + '\n\n' +
        'data: ' + JSON.stringify({ type: 'done', draft_id: null }) + '\n\n',
    })
  })

  await page.goto(BASE, { waitUntil: 'networkidle' })
  const importBtn = page.getByRole('button', { name: /匯入既有憑證/ })
  if (await importBtn.count()) {
    await importBtn.click()
    await page.waitForTimeout(2500)
  }

  console.log('\n【1】切到 Mention 收件匣')
  await page.getByRole('button', { name: /Mention 收件匣/ }).first().click()
  const boxes = page.locator('[role="checkbox"]')
  // **不可以用固定 waitForTimeout 之後直接數**：收件匣是非同步載入的，
  // 慢一點就數到 0，而後面的 click 有自動等待所以會恢復——表現成
  // 「只有第一條斷言偶爾紅」，看起來像功能壞掉。2026-09-07 實際踩到。
  await boxes.first().waitFor({ state: 'visible', timeout: 20000 }).catch(() => {})
  check('待處理清單有 checkbox', (await boxes.count()) > 0, `${await boxes.count()} 個`)

  console.log('\n【2】勾第一則')
  await boxes.nth(0).click()
  await page.waitForTimeout(400)
  const bar = page.getByText(/已選 1 則，一起回成一則/)
  check('合併列出現', (await bar.count()) > 0)
  check(
    '只勾一則時按鈕會說「再勾一則」',
    (await page.getByRole('button', { name: /再勾一則才需要合併/ }).count()) > 0,
  )

  console.log('\n【3】勾第二則（同一個私訊）')
  // **不可以直接點 nth(1)**：收件匣是按時間排序的，而採集器每 45 秒跑一輪，
  // 第 0、1 個未必在同一個 Space——那個假設會讓這支測試偶發失敗，
  // 而且失敗訊息看起來像功能壞掉。
  // 勾第一則之後，不同 Space 的項目會被 app 自己停用，所以「第一個還能勾的」
  // 必然與它同一個 Space。用 app 的規則挑，不用位置猜。
  const enabled = page.locator('[role="checkbox"]:not([data-disabled]):not([disabled])')
  const second = enabled.nth(1) // nth(0) 是剛才勾起來的那個
  if ((await enabled.count()) < 2) {
    check('找得到同一個 Space 的第二則可勾項目', false,
      '這個資料庫目前沒有兩則同 Space 的待處理項目')
  }
  await second.click()
  await page.waitForTimeout(400)
  check('合併列顯示 2 則', (await page.getByText(/已選 2 則/).count()) > 0)
  const mergeBtn = page.getByRole('button', { name: /合併產生草稿（2 則）/ })
  check('合併按鈕可用', (await mergeBtn.count()) > 0 && (await mergeBtn.isEnabled()))

  console.log('\n【4】不同 Space 的項目應該不可勾，而且說得出原因')
  const disabled = await boxes.evaluateAll((els) =>
    els.filter((e) => e.getAttribute('aria-disabled') === 'true' || e.hasAttribute('data-disabled') || e.hasAttribute('disabled')).length,
  )
  check('有項目被停用', disabled > 0, `${disabled} 個`)
  check(
    '畫面上寫出不能合併的原因',
    (await page.getByText(/不同的聊天室，沒辦法用一則回話回完/).count()) > 0,
  )

  console.log('\n【5】按下合併產生')
  await mergeBtn.click()
  await page.waitForTimeout(2000)
  check('request 帶了 merge_mention_ids', Array.isArray(draftBody?.merge_mention_ids), JSON.stringify(draftBody?.merge_mention_ids))
  // URL 上是主要那則，body 只帶「其他」那幾則——兩者相加才是這次要回的總數
  const primaryId = Number((draftUrl || '').match(/mentions\/(\d+)\//)?.[1])
  const total = new Set([primaryId, ...(draftBody?.merge_mention_ids ?? [])]).size
  check('主要那則 ＋ merge_mention_ids 共 2 則', total === 2, `url=${primaryId} body=${JSON.stringify(draftBody?.merge_mention_ids)}`)
  check('草稿工作區顯示「合併回覆 2 則」', (await page.getByText(/合併回覆 2 則/).count()) > 0)

  console.log('\n【6】沒有 console error')
  check('沒有 console error', errs.length === 0, errs.slice(0, 2).join(' | '))

  const pass = results.filter((r) => r.ok).length
  console.log(`\n${'='.repeat(56)}\n  ${pass}/${results.length} 項通過`)
  if (pass !== results.length) {
    console.log('  未通過：')
    results.filter((r) => !r.ok).forEach((r) => console.log(`    - ${r.label} (${r.detail})`))
  }
  console.log('='.repeat(56))
  await b.close()
  process.exit(pass === results.length ? 0 : 1)
})()
