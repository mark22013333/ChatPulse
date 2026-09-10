/**
 * E2E：介面改版的核心流程（真瀏覽器）。
 *
 * 為什麼這一支要存在，而不是靠元件測試就好：
 *
 * 改版的三個主角——hash 路由、常駐掛載的兩個工作台、覆蓋層式的設定中心
 * ——全都建立在**瀏覽器歷史**與**真實焦點**上。元件測試跑在 jsdom，
 * 它的 history 是模擬的、沒有佈局、也沒有真的 tab 序。設定頁那個「點五個
 * 分頁要按五次關閉」的 bug 就是最好的例子：它的元件測試是綠的，但那個綠
 * 只證明 jsdom 的 history 行為對，證明不了真瀏覽器對。
 *
 * 這支測試守住七件事：
 *   1. 深連結直接落在對的工作台（重新整理停在原地）
 *   2. **點過 N 個設定分頁之後，按一次「關閉」就回到原本的位置**
 *   3. 在設定裡按瀏覽器返回鍵，一次就離開設定
 *   4. 直接貼設定連結進來，關閉落到 #/summary（不是白畫面）
 *   5. Esc 由內而外：面板開著時只關面板，不連設定一起關
 *   6. ⌘K 命令面板：開啟、過濾、Enter 導航、Esc 還焦點
 *   7. Space 清單的 roving tabindex：整份清單只占一個 Tab 停留點
 *
 * **這支測試不觸發任何不可逆或燒配額的動作**：不按送出回話、不按標記已
 * 處理、不按產生 Draft Reply。合併勾選只改前端狀態與網址。
 *
 * 前置條件：服務已執行（`./chatpulse.sh web`，預設 127.0.0.1:8000）且
 * 資料庫裡有授權過的 Viewer。認證方式見 e2e_browser.cjs 的說明——
 * 設了 CHATPULSE_SESSION 就零資料庫寫入。
 *
 * 用法：node tests/e2e/test_ui_redesign.cjs
 */

const { open, scoreboard, goHash, hashOf } = require('./e2e_browser.cjs')

/** 設定中心的六個分頁（照畫面上的順序）。 */
const SETTINGS_TABS = ['Persona', '常用提示詞', '參考專案', 'Space', '資料']

/**
 * 選擇器一律限縮，理由是**兩個工作台常駐掛載**（規格 §7.2）：看不見的那一半
 * 仍然在 DOM 裡，而 Playwright 的 `name` 預設是**子字串**比對，所以
 * `getByRole('button', { name: '設定' })` 會連收件匣裡「內文剛好提到設定」
 * 的 Mention 卡片一起選中，撞上 strict mode。2026-09-10 實際踩到——而且它
 * 是**資料相關的偶發**：換一批 Mention 就不會發生，看起來像功能壞掉。
 *
 * 兩條規則：
 *   1. 頂列的按鈕用 `exact: true`，或先限縮到 `header`
 *   2. 清單用它自己的 aria-label（改版時就是為此加上去的），不要用 `.first()`
 */
const SUMMARY_LISTBOX = '[role="listbox"][aria-label="要做摘要的 Space"]'

const topBarButton = (page, name) => page.locator('header').getByRole('button', { name })
const settingsDialog = (page) => page.getByRole('dialog', { name: '設定' })

;(async () => {
  const { browser, page, errors, authMode } = await open()
  const { check, finish } = scoreboard()
  console.log(`  （認證方式：${authMode}）`)

  // ── 1. 深連結 ──────────────────────────────────────────────
  console.log('\n【1】深連結直接落在對的工作台')
  await goHash(page, '#/mentions')
  check(
    '#/mentions 落在收件匣',
    (await page.getByRole('heading', { name: 'Mention 收件匣' }).count()) > 0,
  )

  await goHash(page, '#/summary')
  check(
    '#/summary 落在摘要工作台',
    (await topBarButton(page, /摘要工作台/).count()) > 0,
  )

  // 記住一個「原本在哪」的位置，後面要驗關閉設定會回到這裡
  const firstSpaceRow = page.locator(`${SUMMARY_LISTBOX} [role="option"][data-option-index]`).first()
  let origin = '#/summary'
  if (await firstSpaceRow.count()) {
    await firstSpaceRow.click()
    await page.waitForTimeout(400)
    origin = await hashOf(page)
  }
  check('點一個 Space 之後網址帶得走', origin.startsWith('#/summary/'), origin)

  // ── 2. 設定頁點 N 個分頁，按一次關閉 ──────────────────────
  console.log('\n【2】點過五個設定分頁之後，按一次「關閉」')
  await topBarButton(page, '設定').click()
  await page.waitForTimeout(400)
  check('進入設定中心', (await hashOf(page)) === '#/settings/reply')

  // 正對照：先量一次 push 導覽讓 history.length 真的會動。
  // 少了這條，下面「切分頁 +0」也可能只是「這個環境量不到 history.length」。
  const lenBeforeTabs = await page.evaluate(() => history.length)
  for (const label of SETTINGS_TABS) {
    await page.getByRole('link', { name: new RegExp('^' + label) }).click()
    await page.waitForTimeout(180)
  }
  const lenAfterTabs = await page.evaluate(() => history.length)
  check('五個分頁都點得到（最後停在「資料」）', (await hashOf(page)) === '#/settings/data')
  check(
    '**切五個分頁只占一筆歷史**',
    lenAfterTabs - lenBeforeTabs === 0,
    `history.length ${lenBeforeTabs} → ${lenAfterTabs}`,
  )

  await settingsDialog(page).getByRole('button', { name: '關閉' }).click()
  await page.waitForTimeout(450)
  check(
    '**按一次「關閉」就回到原本的位置**',
    (await hashOf(page)) === origin,
    `期望 ${origin}，實際 ${await hashOf(page)}`,
  )
  check('設定覆蓋層真的消失了', (await settingsDialog(page).count()) === 0)

  // ── 3. 返回鍵一次離開設定 ─────────────────────────────────
  console.log('\n【3】在設定裡按瀏覽器返回鍵')
  await topBarButton(page, '設定').click()
  await page.waitForTimeout(350)
  await page.getByRole('link', { name: /^Persona/ }).click()
  await page.waitForTimeout(250)
  await page.goBack()
  await page.waitForTimeout(450)
  const afterBack = await hashOf(page)
  check(
    '**返回鍵一次就離開設定**（不是逐個分頁退）',
    !afterBack.startsWith('#/settings'),
    afterBack,
  )

  // ── 4. 直接貼設定連結（要是**整頁重載**才算） ─────────────
  console.log('\n【4】直接貼設定連結進來（新開分頁的情境）')
  // `page.goto(base + '#/settings/...')` 在同一份文件上只是換 hash，SPA 不會
  // 重建，router 記著的「進設定之前在哪」還在——那是**另一個**情境（app 內
  // 導覽），關閉會正確回到原位。要模擬「貼連結開新分頁」必須真的重載，
  // 讓 lastNonSettings 回到初始值。
  await goHash(page, '#/settings/personas')
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(700)
  check('直接開得起來', (await settingsDialog(page).count()) > 0)
  check('重載後仍停在設定（重新整理停在原地）', (await hashOf(page)) === '#/settings/personas')
  await settingsDialog(page).getByRole('button', { name: '關閉' }).click()
  await page.waitForTimeout(450)
  check(
    '關閉落到 #/summary（不是白畫面）',
    (await hashOf(page)) === '#/summary',
    await hashOf(page),
  )

  // ── 5. Esc 由內而外 ───────────────────────────────────────
  console.log('\n【5】Esc 由內而外：面板開著時只關面板')
  await topBarButton(page, '設定').click()
  await page.waitForTimeout(350)
  await page.keyboard.press('Meta+k')
  await page.waitForTimeout(300)
  check('設定開著時也開得了命令面板', (await page.getByRole('dialog', { name: '命令面板' }).count()) > 0)

  await page.keyboard.press('Escape')
  await page.waitForTimeout(350)
  check(
    '**Esc 只關面板，設定還在**',
    (await page.getByRole('dialog', { name: '命令面板' }).count()) === 0 &&
      (await settingsDialog(page).count()) > 0,
  )

  // 正對照：面板關掉之後，同一個按鍵確實關得掉設定
  await page.keyboard.press('Escape')
  await page.waitForTimeout(400)
  check(
    '正對照：再按一次 Esc 關掉設定',
    (await settingsDialog(page).count()) === 0,
  )

  // ── 6. 命令面板 ───────────────────────────────────────────
  console.log('\n【6】⌘K 命令面板')
  await goHash(page, '#/summary')
  await page.keyboard.press('Meta+k')
  await page.waitForTimeout(300)
  const search = page.getByRole('combobox', { name: '搜尋指令' })
  check('⌘K 開啟面板', (await search.count()) > 0)
  check(
    '焦點在搜尋框',
    await page.evaluate(() => document.activeElement?.getAttribute('aria-label') === '搜尋指令'),
  )

  await search.fill('設定')
  await page.waitForTimeout(300)
  const optionCount = await page.locator('#command-results [role="option"]').count()
  check('打字會過濾出結果', optionCount > 0, `${optionCount} 項`)
  check(
    '高亮由 aria-activedescendant 標示（焦點不離開輸入框）',
    Boolean(await search.getAttribute('aria-activedescendant')),
  )

  await page.keyboard.press('ArrowDown')
  await page.waitForTimeout(150)
  check(
    '↓ 之後焦點仍在輸入框',
    await page.evaluate(() => document.activeElement?.getAttribute('aria-label') === '搜尋指令'),
  )

  await search.fill('送出')
  await page.waitForTimeout(300)
  check(
    '**搜「送出」查無結果**（不可撤回的動作不進面板）',
    (await page.getByText(/找不到「送出」/).count()) > 0,
  )

  await page.keyboard.press('Escape')
  await page.waitForTimeout(300)
  check('Esc 關閉面板', (await page.getByRole('dialog', { name: '命令面板' }).count()) === 0)

  // ── 7. 虛擬清單的 roving tabindex ─────────────────────────
  console.log('\n【7】Space 清單的 roving tabindex')
  await goHash(page, '#/summary')
  const listbox = page.locator(SUMMARY_LISTBOX)
  check('Space 清單是有名字的 listbox', (await listbox.count()) === 1, await listbox.getAttribute('aria-label'))

  const tabbable = await page.evaluate((sel) => {
    const box = document.querySelector(sel)
    if (!box) return -1
    return [...box.querySelectorAll('[role="option"]')].filter(
      (el) => el.getAttribute('tabindex') === '0',
    ).length
  }, SUMMARY_LISTBOX)
  check('**整份清單只有一個 tabIndex=0**', tabbable === 1, `${tabbable} 個`)

  const moved = await page.evaluate(async (sel) => {
    const box = document.querySelector(sel)
    const first = box?.querySelector('[role="option"][tabindex="0"]')
    if (!(first instanceof HTMLElement)) return null
    first.focus()
    const before = document.activeElement?.getAttribute('data-option-index')
    first.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    await new Promise((r) => setTimeout(r, 250))
    return { before, after: document.activeElement?.getAttribute('data-option-index') }
  }, SUMMARY_LISTBOX)
  check(
    '↓ 把焦點移到下一列',
    moved !== null && moved.before !== moved.after && moved.after !== null,
    JSON.stringify(moved),
  )

  // ── 8. 串流宣告的 live region ─────────────────────────────
  console.log('\n【8】串流的 live region 常駐（規格 §10.6）')
  const regions = await page.evaluate(() => ({
    status: document.querySelectorAll('[role="status"][aria-live="polite"]').length,
    alert: document.querySelectorAll('[role="alert"]').length,
    // 內容層絕對不可以有 aria-live——每個 chunk 都重寫 innerHTML，
    // 設了等於整段重念
    markdownLive: document.querySelectorAll('.markdown-body[aria-live]').length,
  }))
  // **不觸發串流**（那會燒 AI 配額）。這裡只驗「region 在內容寫進去之前就
  // 存在」——那是 live region 最常見的壞法，而它在畫面上完全看不出來。
  check('app 級 role="status" 常駐', regions.status >= 1, `${regions.status} 個`)
  check('錯誤用的 role="alert" 常駐', regions.alert >= 1, `${regions.alert} 個`)
  check(
    '**Markdown 容器沒有 aria-live**',
    regions.markdownLive === 0,
    `${regions.markdownLive} 個`,
  )

  // ── 9. 沒有 console error ─────────────────────────────────
  console.log('\n【9】沒有 console error')
  check('沒有 console error', errors.length === 0, errors.slice(0, 2).join(' | '))

  const code = finish()
  await browser.close()
  process.exit(code)
})().catch((err) => {
  console.error('\n測試本身炸了（不是斷言失敗）：', err)
  process.exit(2)
})
