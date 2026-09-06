/**
 * E2E：摘要工作台的「最近訊息」預覽。
 *
 * 需求：點左邊清單的一個 Space，就看得到最近 10／20／30 則訊息（可選），
 * **也要包含討論串**。在此之前必須先跑一次摘要（燒 AI 額度）才知道裡面在講什麼。
 *
 * 這支測試要守住六件事：
 *   1. 沒選 Space 時不出現面板
 *   2. 點了 Space 會打 /api/v1/messages，預設 20 則
 *   3. 10／20／30 切換會重打並真的改變顯示則數
 *   4. **討論串在外層只佔一列**，點開才看得到內容。點開時順便補齊被 limit
 *      切掉的部分——而且是按了才打 API，不預先撈（一個視窗可能有二十幾串）
 *   5. **同一則訊息不會出現兩次**（用 data-message-name 直接驗）。
 *      這是使用者回報的缺陷：外層把整串每一則印一次、點開又印一次
 *   6. 私訊每則各自成一串，不可以整排都被收合（那等於沒有清單）
 *   7. **純圖片、沒有文字的訊息要看得見**。這個端點原本 `if not text: continue`，
 *      那種訊息會整則消失，表現是「我要 20 則怎麼只有 17 則」而找不到原因
 *
 * 全程唯讀，不寫任何東西、不燒 AI 額度。
 *
 * 前置條件：服務已執行、資料庫裡有授權過的 Viewer，且清單裡有
 * 「P.S.公部門夥伴」（要有真討論串的群組）與「李姿誼」（私訊）。
 * 換別台機器跑要改下面兩個名字。
 *
 * 用法：node tests/e2e/test_message_preview.cjs
 */

const path = require('path')
const { execSync } = require('child_process')
const globalRoot = execSync('npm root -g').toString().trim()
const { chromium } = require(path.join(globalRoot, 'playwright'))

//: 這台機器上已知的樣本。群組要有多則的討論串，私訊要有圖片附件。
const GROUP_WITH_THREADS = process.env.PREVIEW_GROUP || 'P.S.公部門夥伴'
const DM_WITH_IMAGES = process.env.PREVIEW_DM || '李姿誼'

const BASE = process.env.CHATPULSE_URL || 'http://127.0.0.1:8010'
const results = []
const check = (label, ok, detail = '') => {
  results.push({ label, ok, detail })
  console.log(`  ${ok ? '✓' : '✗'} ${label}${detail ? ' — ' + detail : ''}`)
}

;(async () => {
  const b = await chromium.launch()
  const page = await b.newPage({ viewport: { width: 1500, height: 1000 } })
  const errs = []
  page.on('pageerror', (e) => errs.push(e.message))
  page.on('console', (m) => {
    if (m.type() === 'error') errs.push(m.text())
  })
  const msgCalls = []
  page.on('request', (r) => {
    if (r.url().includes('/api/v1/messages')) msgCalls.push(r.url())
  })

  await page.goto(BASE, { waitUntil: 'networkidle' })
  const importBtn = page.getByRole('button', { name: /匯入既有憑證/ })
  if (await importBtn.count()) {
    await importBtn.click()
    await page.waitForTimeout(2500)
  }

  console.log('\n【1】還沒選 Space 時不該出現預覽')
  check('沒有「最近訊息」面板', (await page.getByText('最近訊息').count()) === 0)

  console.log('\n【2】點一個群組（有真正的討論串）')
  await page.getByText(GROUP_WITH_THREADS).first().click()
  await page.waitForTimeout(3000)
  check('出現「最近訊息」面板', (await page.getByText('最近訊息').count()) > 0)
  check('有打 /api/v1/messages', msgCalls.length > 0, msgCalls[0]?.split('/api')[1])
  check('預設抓 20 則', (msgCalls[0] ?? '').includes('limit=20'), msgCalls[0]?.split('?')[1])

  const header = await page.locator('section:has-text("最近訊息")').first().innerText()
  console.log('    面板標頭：', header.split('\n').slice(0, 4).join(' | '))
  check('顯示則數與討論串數', /顯示 \d+ 則/.test(header), header.match(/顯示 \d+ 則[^\n]*/)?.[0])
  check('偵測到討論串', /\d+ 個討論串/.test(header), header.match(/\d+ 個討論串/)?.[0])

  console.log('\n【3】切成 10 則')
  await page.getByRole('button', { name: '10', exact: true }).click()
  await page.waitForTimeout(2500)
  const last = msgCalls[msgCalls.length - 1]
  check('重新打 API 且 limit=10', last.includes('limit=10'), last.split('?')[1])
  const header10 = await page.locator('section:has-text("最近訊息")').first().innerText()
  const shown = Number(header10.match(/顯示 (\d+) 則/)?.[1] ?? -1)
  check('實際顯示 ≤ 10 則', shown > 0 && shown <= 10, `${shown} 則`)

  console.log('\n【4】切成 30 則')
  await page.getByRole('button', { name: '30', exact: true }).click()
  await page.waitForTimeout(2500)
  const header30 = await page.locator('section:has-text("最近訊息")').first().innerText()
  const shown30 = Number(header30.match(/顯示 (\d+) 則/)?.[1] ?? -1)
  check('30 則比 10 則多', shown30 > shown, `${shown} → ${shown30}`)

  console.log('\n【5】討論串在外層只佔一列，點開才看得到內容')
  const panel = page.locator('section:has-text("最近訊息")').first()
  const threadRow = panel.locator('button[aria-expanded="false"]:has-text("討論串")').first()
  if (await threadRow.count()) {
    const collapsedText = await panel.innerText()
    // 收合時整串的內容不該出現在外層——這正是使用者回報的重複問題
    const rowLabel = await threadRow.innerText()
    const threadLen = Number(rowLabel.match(/討論串 (\d+) 則/)?.[1] ?? 0)
    check('收合列寫出這一串有幾則', threadLen >= 2, `${threadLen} 則`)

    const beforeCalls = msgCalls.length
    await threadRow.click()
    await page.waitForTimeout(2500)
    const added = msgCalls.slice(beforeCalls)
    check('按了才打 API（不預先撈整串）', added.length === 1, added[0]?.split('?')[1]?.slice(0, 80))
    check('帶了 thread_name', (added[0] ?? '').includes('thread_name='))

    const expandedText = await panel.innerText()
    check('點開後畫面變長（內容真的展開了）', expandedText.length > collapsedText.length,
      `${collapsedText.length} → ${expandedText.length} 字`)
    check(
      '變成可收起（aria-expanded=true）',
      (await panel.locator('button[aria-expanded="true"]:has-text("討論串")').count()) > 0,
    )

    // 再點一次要收回去，而且長度回到原本
    await panel.locator('button[aria-expanded="true"]:has-text("討論串")').first().click()
    await page.waitForTimeout(600)
    check('再點一次收回去', (await panel.innerText()).length <= collapsedText.length + 5)
  } else {
    check('找到可展開的討論串', false, '這個 Space 目前沒有多則的討論串')
  }

  console.log('\n【6】切到私訊：每則各自一串，不該整排都被收合')
  msgCalls.length = 0
  await page.getByText(DM_WITH_IMAGES).first().click()
  await page.waitForTimeout(3000)
  const dmHeader = await page.locator('section:has-text("最近訊息")').first().innerText()
  console.log('    私訊面板：', dmHeader.split('\n').slice(0, 4).join(' | '))
  check('換 Space 有重新讀取', msgCalls.length > 0)
  check('私訊沒有被標成一堆討論串', !/[5-9]\d* 個討論串/.test(dmHeader), dmHeader.match(/\d+ 個討論串/)?.[0] ?? '0 個')

  console.log('\n【7】同一則訊息不會出現兩次（使用者回報的重複問題）')
  const dupCheck = async (tag) => {
    const names = await page
      .locator('section:has-text("最近訊息") [data-message-name]')
      .evaluateAll((els) => els.map((e) => e.getAttribute('data-message-name')))
    const seen = new Set(names)
    check(`${tag}：沒有重複的訊息`, seen.size === names.length, `${names.length} 列 / ${seen.size} 則不重複`)
  }
  await dupCheck('收合時')
  const anyThread = page
    .locator('section:has-text("最近訊息") button[aria-expanded="false"]:has-text("討論串")')
    .first()
  if (await anyThread.count()) {
    await anyThread.click()
    await page.waitForTimeout(2500)
    await dupCheck('展開後')
  }

  console.log('\n【8】純圖片訊息看得見')
  const bodyText = await page.locator('section:has-text("最近訊息")').first().innerText()
  check('畫面上看得到圖片附件標示', bodyText.includes('[圖片'), bodyText.match(/\[圖片[^\]]{0,30}/)?.[0] ?? '無')

  console.log('\n【9】沒有 console error')
  check('沒有 console error', errs.length === 0, errs.slice(0, 2).join(' | '))

  const pass = results.filter((r) => r.ok).length
  console.log(`\n${'='.repeat(56)}\n  ${pass}/${results.length} 項通過`)
  results.filter((r) => !r.ok).forEach((r) => console.log(`    ✗ ${r.label} (${r.detail})`))
  console.log('='.repeat(56))
  await b.close()
  process.exit(pass === results.length ? 0 : 1)
})()
