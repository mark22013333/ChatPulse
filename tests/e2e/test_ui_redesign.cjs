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

/**
 * 把焦點從輸入框拿掉再測單鍵快捷鍵。
 *
 * 單鍵快捷鍵在 `input`／`textarea`／`[contenteditable]` 裡刻意不生效
 * （規格 §11.2）。前面幾節點過搜尋框與分頁連結，焦點很可能還在某個輸入框上
 * ——不先 blur 的話，`g s` 會變成在搜尋框裡打字，而失敗訊息長得像「快捷鍵
 * 沒實作」。
 */
const blur = (page) =>
  page.evaluate(() => {
    const el = document.activeElement
    if (el instanceof HTMLElement) el.blur()
  })

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

  // ── 9. `?` 快捷鍵說明 ─────────────────────────────────────
  console.log('\n【9】? 快捷鍵說明（規格 §11.1）')
  await goHash(page, '#/summary')
  await blur(page)
  await page.keyboard.press('?')
  await page.waitForTimeout(300)
  const helpDialog = page.getByRole('dialog', { name: '鍵盤快捷鍵' })
  check('? 開啟說明面板', (await helpDialog.count()) > 0)
  const kbdCount = await helpDialog.locator('kbd').count()
  check('說明面板逐鍵畫成 kbd', kbdCount >= 20, `${kbdCount} 個 kbd`)
  check(
    '焦點進了面板（鍵盤使用者不會還停在後面的工作台）',
    await page.evaluate(
      () => document.activeElement?.getAttribute('aria-label') === '鍵盤快捷鍵',
    ),
  )
  await page.keyboard.press('Escape')
  await page.waitForTimeout(300)
  check('Esc 關閉說明面板', (await helpDialog.count()) === 0)

  // 這一條是本輪最重要的迴歸：設定開著時多開一層說明，一次 Esc 只能關一層。
  // 同型的 bug 在命令面板上發生過（commit adf7c84），修法是 defaultPrevented
  // ＋ store 的 isOverlayOpen()。新增覆蓋層時最容易漏掉的就是這件事。
  await topBarButton(page, '設定').click()
  await page.waitForTimeout(400)
  await blur(page)
  await page.keyboard.press('?')
  await page.waitForTimeout(300)
  check('設定開著時也開得了說明面板', (await helpDialog.count()) > 0)
  await page.keyboard.press('Escape')
  await page.waitForTimeout(400)
  check(
    '**Esc 只關說明面板，設定還在**',
    (await helpDialog.count()) === 0 && (await settingsDialog(page).count()) > 0,
  )
  await page.keyboard.press('Escape')
  await page.waitForTimeout(400)
  check('正對照：再按一次 Esc 才關掉設定', (await settingsDialog(page).count()) === 0)

  // ── 10. 兩鍵序列 g s / g m / g , / g h ────────────────────
  console.log('\n【10】兩鍵序列導覽（規格 §11.1）')
  await goHash(page, '#/mentions')
  await blur(page)
  await page.keyboard.press('g')
  await page.keyboard.press('s')
  await page.waitForTimeout(300)
  check('g s → 摘要工作台', (await hashOf(page)) === '#/summary', await hashOf(page))

  await page.keyboard.press('g')
  await page.keyboard.press('m')
  await page.waitForTimeout(300)
  check('g m → Mention 收件匣', (await hashOf(page)) === '#/mentions', await hashOf(page))

  await page.keyboard.press('g')
  await page.keyboard.press(',')
  await page.waitForTimeout(400)
  check('g , → 設定', (await hashOf(page)) === '#/settings/reply', await hashOf(page))
  await page.keyboard.press('Escape')
  await page.waitForTimeout(400)

  // g h 落在診斷頁。**診斷頁排在登入 gate 之前、由 App 直接渲染**（規格 §6.5），
  // 所以它不在 AppShell 裡、全域快捷鍵在那一頁不生效——要離開得用畫面上的
  // 「回到…」按鈕。這是既有的架構決定，不是這次的 bug，但值得留在測試裡當紀錄。
  await goHash(page, '#/mentions')
  await blur(page)
  await page.keyboard.press('g')
  await page.keyboard.press('h')
  await page.waitForTimeout(500)
  check('g h → 診斷頁', (await hashOf(page)) === '#/settings/diagnostics', await hashOf(page))
  check('診斷頁有離開的入口（那一頁沒有全域快捷鍵）', (await page.getByRole('button', { name: /回到/ }).count()) > 0)

  // 逾時：`g` 之後超過 1 秒才按第二鍵就不算一組
  await goHash(page, '#/mentions')
  await blur(page)
  await page.keyboard.press('g')
  await page.waitForTimeout(1300)
  await page.keyboard.press('s')
  await page.waitForTimeout(300)
  check('**超過 1 秒的第二鍵不算同一組**', (await hashOf(page)) === '#/mentions', await hashOf(page))

  // 正對照：同一組按鍵不等待就會動——證明上面那個「沒動」不是按鍵根本沒送到
  await page.keyboard.press('g')
  await page.keyboard.press('s')
  await page.waitForTimeout(300)
  check('正對照：不等待時同一組按鍵是會動的', (await hashOf(page)) === '#/summary', await hashOf(page))

  // 在左欄搜尋框裡打字不該觸發導覽
  await goHash(page, '#/summary')
  const railSearch = page.locator('aside input[type="text"], aside input:not([type])').first()
  await railSearch.click()
  await railSearch.fill('')
  await page.keyboard.type('gs')
  await page.waitForTimeout(300)
  check(
    '**在搜尋框裡打 g s 只是打字**（值真的收到了，才證明按鍵有送到）',
    (await hashOf(page)) === '#/summary' && (await railSearch.inputValue()) === 'gs',
    `hash=${await hashOf(page)} value=${await railSearch.inputValue()}`,
  )
  await railSearch.fill('')

  // ── 11. [ ] 換 Mention 與收件匣清單的 ↑↓ ─────────────────
  console.log('\n【11】[ ] 換 Mention、收件匣清單的 ↑↓（規格 §11.1）')
  await goHash(page, '#/mentions')
  await page.waitForTimeout(400)
  // 待處理可能只有一兩則，資料不夠就換到已處理分頁（那裡筆數多得多）
  let cards = page.locator('[data-mention-index]')
  if ((await cards.count()) < 2) {
    await page.getByRole('tab', { name: /已處理/ }).click()
    await page.waitForTimeout(500)
    cards = page.locator('[data-mention-index]')
  }
  const cardCount = await cards.count()
  check('收件匣至少有兩則可以走（否則下面兩條測不出東西）', cardCount >= 2, `${cardCount} 則`)

  if (cardCount >= 2) {
    await cards.first().click()
    await page.waitForTimeout(400)
    const firstHash = await hashOf(page)
    await blur(page)
    await page.keyboard.press(']')
    await page.waitForTimeout(450)
    const nextHash = await hashOf(page)
    check('] 走到下一則', nextHash !== firstHash && nextHash.startsWith('#/mentions/'), `${firstHash} → ${nextHash}`)

    await page.keyboard.press('[')
    await page.waitForTimeout(450)
    check('[ 走得回上一則', (await hashOf(page)) === firstHash, await hashOf(page))

    // 已經在第一則，再按 [ 應該停住（邊界夾住、不繞回）
    await page.keyboard.press('[')
    await page.waitForTimeout(400)
    check('**在第一則按 [ 停住，不繞回最後一則**', (await hashOf(page)) === firstHash, await hashOf(page))

    // 清單的 ↑↓ 只移動焦點、不改網址
    const arrow = await page.evaluate(async () => {
      const first = document.querySelector('[data-mention-index="0"]')
      if (!(first instanceof HTMLElement)) return null
      first.focus()
      const hashBefore = window.location.hash
      first.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
      await new Promise((r) => setTimeout(r, 250))
      return {
        focused: document.activeElement?.getAttribute('data-mention-index'),
        hashBefore,
        hashAfter: window.location.hash,
      }
    })
    check('↓ 把焦點移到下一則', arrow !== null && arrow.focused === '1', JSON.stringify(arrow))
    check(
      '**↓ 不改網址**（移動與開啟要分開，不然走過十則就是十筆歷史）',
      arrow !== null && arrow.hashBefore === arrow.hashAfter,
      JSON.stringify(arrow),
    )
  }

  // ── 12. ⌘⇧C 複製 Markdown ────────────────────────────────
  console.log('\n【12】⌘⇧C 複製 Markdown（規格 §11.1）')
  // **不觸發串流**，所以這裡沒有摘要內容可以複製。那正好是可觀察的訊號：
  // 快捷鍵有接上就會冒「沒有可複製的內容」，完全沒接上則什麼都不會出現。
  // 裸 ⌘C 不可以被攔這件事由 `hooks/useGlobalHotkeys.test.tsx` 守著。
  await goHash(page, '#/summary')
  await blur(page)
  await page.keyboard.press('Meta+Shift+C')
  await page.waitForTimeout(600)
  const copyToast = await page.getByText(/已複製摘要 Markdown|沒有可複製的內容/).count()
  check('⌘⇧C 有接上（冒出複製結果的提示）', copyToast > 0)

  // ── 13. 768–1024 的主從切換 ──────────────────────────────
  console.log('\n【13】768–1024 主從切換（規格 §12）')
  // **這一節只有真瀏覽器測得出來**：jsdom 沒有佈局也不算 media query。
  //
  // 收起來的那一半用的是 §7.2 那套手法（留著掛載、移出版面流、`inert`），
  // **不是 `display:none`**——所以不可以用 Playwright 的 `isVisible()` 判斷：
  // `opacity-0` 對它仍然算「可見」，四條斷言會全部假通過。要看的是
  // computed opacity 與 `inert` 屬性。
  const paneState = () =>
    page.evaluate(() => {
      const read = (el) => {
        if (!el) return null
        const style = getComputedStyle(el)
        return {
          shown: style.opacity !== '0' && style.display !== 'none',
          inert: el.hasAttribute('inert'),
          width: Math.round(el.getBoundingClientRect().width),
        }
      }
      return {
        list: read(document.querySelector('aside#rail')),
        main: read(document.querySelector('main#main')),
      }
    })
  const onlyOne = (panes, which) => {
    const shown = which === 'list' ? panes.list : panes.main
    const hidden = which === 'list' ? panes.main : panes.list
    return shown.shown && !shown.inert && !hidden.shown && hidden.inert
  }

  await page.setViewportSize({ width: 900, height: 800 })
  await goHash(page, '#/summary')
  await page.waitForTimeout(400)
  let panes = await paneState()
  check(
    '**900px＋沒選 Space：只顯示清單，清單占滿寬度**',
    onlyOne(panes, 'list') && panes.list.width > 700,
    JSON.stringify(panes),
  )
  check('清單那一半沒有麵包屑（清單就是最外層，沒有上一層）', (await page.getByRole('navigation', { name: '麵包屑' }).count()) === 0)

  const rowCount = await page.locator(`${SUMMARY_LISTBOX} [role="option"][data-option-index]`).count()
  if (rowCount > 0) {
    // 先捲到清單中段，等一下要驗「進工作區再退回來，捲動位置沒有跳掉」。
    // 停在 0 的話那條測不出東西——0 本來就不會「掉」。
    const scrollTopOf = () =>
      page.evaluate((sel) => document.querySelector(sel)?.parentElement?.scrollTop ?? -1, SUMMARY_LISTBOX)
    await page.evaluate((sel) => {
      const scroller = document.querySelector(sel)?.parentElement
      if (scroller) scroller.scrollTop = 3000
    }, SUMMARY_LISTBOX)
    await page.waitForTimeout(400)

    // **用 JS 的 element.click() 而不是 Playwright 的 click()**：後者會先把元素
    // 聚焦，而聚焦一個部分捲出可視範圍的列會讓瀏覽器把它捲進來——實測
    // scrollTop 因此從 3026 變成 2082。那個位移與「隱藏那一半」無關，混在
    // 一起量會得到一個假的「捲動位置掉了」（HANDOFF 第四節：自製證據要先
    // 確認自己量的是想量的東西）。
    await page.evaluate((sel) => {
      const opts = [...document.querySelectorAll(`${sel} [role="option"][data-option-index]`)]
      opts[Math.floor(opts.length / 2)]?.click()
    }, SUMMARY_LISTBOX)
    await page.waitForTimeout(600)
    const scrollBefore = await scrollTopOf()
    check('清單捲得動（下一條要用它當基準）', scrollBefore > 2000, `scrollTop=${scrollBefore}`)

    panes = await paneState()
    check(
      '**點一個 Space 之後換成只顯示工作區**',
      onlyOne(panes, 'main'),
      JSON.stringify(panes),
    )

    const crumb = page.getByRole('button', { name: '返回 Space 清單' })
    check('工作區有回得去的麵包屑', (await crumb.count()) === 1)
    await crumb.click()
    await page.waitForTimeout(700)
    panes = await paneState()
    check('**按麵包屑回到清單**', onlyOne(panes, 'list'), `${await hashOf(page)} ${JSON.stringify(panes)}`)

    // 規格 §7.2 的理由在這裡再現一次：虛擬清單不可以用 display:none 藏。
    // 2026-09-10 第一版用了 max-lg:hidden，實測捲動位置從 0 自己跳到 1296。
    const scrollAfter = await scrollTopOf()
    check(
      '**退回清單之後捲動位置沒有跳掉**（虛擬清單不可以用 display:none 藏）',
      Math.abs(scrollAfter - scrollBefore) < 60,
      `${scrollBefore} → ${scrollAfter}`,
    )

    // 這一條顧的是紅線 4 的同族問題：退回清單不可以把選取洗掉，否則從摘要
    // 工作台建立的草稿目標會找不回來。
    // **不驗 aria-selected**：那一列可能捲出可視範圍、根本不在 DOM 裡，
    // 量到 0 說明不了任何事（HANDOFF 第四節第 1 條）。改按頂列的摘要頁籤
    // ——它會帶著 store 記住的 selectedId 導覽，網址就是證據。
    await topBarButton(page, /摘要工作台/).click()
    await page.waitForTimeout(500)
    check(
      '**退回清單沒有把選取洗掉**（頂列頁籤仍回到原本那個 Space）',
      (await hashOf(page)).startsWith('#/summary/'),
      await hashOf(page),
    )
  }

  // 正對照：拉回 1300px，兩欄同時看得見——證明上面的「只顯示一個」是斷點
  // 造成的，不是某一半根本壞掉
  await page.setViewportSize({ width: 1300, height: 800 })
  await goHash(page, '#/summary')
  await page.waitForTimeout(400)
  const row1300 = page.locator(`${SUMMARY_LISTBOX} [role="option"][data-option-index]`).first()
  if (await row1300.count()) {
    await row1300.click()
    await page.waitForTimeout(500)
  }
  panes = await paneState()
  check(
    '正對照：1300px 下清單與工作區同時看得見、兩邊都不是 inert',
    panes.list.shown &&
      panes.main.shown &&
      !panes.list.inert &&
      !panes.main.inert &&
      panes.list.width < 400,
    JSON.stringify(panes),
  )
  check(
    '1300px 下不出現麵包屑（清單就在左邊，多一條是噪音）',
    (await page.getByRole('navigation', { name: '麵包屑' }).count()) === 0,
  )

  // ── 14. 沒有 console error ────────────────────────────────
  console.log('\n【14】沒有 console error')
  check('沒有 console error', errors.length === 0, errors.slice(0, 2).join(' | '))

  const code = finish()
  await browser.close()
  process.exit(code)
})().catch((err) => {
  console.error('\n測試本身炸了（不是斷言失敗）：', err)
  process.exit(2)
})
