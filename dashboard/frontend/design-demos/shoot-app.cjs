/**
 * 截**真實建置產物**的四個畫面（收件匣／摘要／草稿／設定），明暗各一張。
 *
 *   cd dashboard/frontend/dist && python3 -m http.server 8799 &
 *   NODE_PATH=$(npm root -g) node design-demos/shoot-app.js
 *
 * ### 為什麼要 mock API
 *
 * AppShell 要 `authenticated` 才渲染，而真實登入需要 Google OAuth。這裡攔截
 * `/api/v1/**` 餵假資料，只為了讓版面長出來——不是為了測資料流。要看的是
 * **表面分層與卡片語彙在真實元件上的樣子**，那是設計稿保證不了的。
 *
 * ### 這支為什麼放在版控裡
 *
 * 它第一版寫在 scratchpad，session 一換就沒了，得重寫。會重複使用的驗證工具
 * 要進版控——每次動 UI 都要再跑一次。
 *
 * ### 兩個踩過的坑
 *
 * 1. **欄位形狀猜錯不會報錯，只會靜默沒東西**。Space 用 camelCase
 *    （`displayName`／`lastActiveTime`，見 lib/types.ts），Mention 用 snake_case
 *    （`space_name`／`sender_display`／`create_time`）。第一版把 Space 寫成
 *    snake_case，清單直接空掉、底部顯示「顯示 0 / 436」。
 * 2. **切主題要點它自己的按鈕**，不要 `classList.add('dark')`——那會繞過
 *    next-themes 的狀態，依賴 JS 狀態的東西不會更新，看起來像元件有 bug。
 */
const { chromium } = require('playwright')
const path = require('path')
const fs = require('fs')

const BASE = process.env.BASE || 'http://localhost:8799'
const OUT = process.env.OUT || path.join(__dirname, 'app-shots')

const SPACES = [
  ['spaces/aaa1', '1.BU2-PG', 'ROOM'],
  ['spaces/aaa2', 'ILOOP2601 - 勞動部勞動及職業安全衛生研究所', 'GROUP'],
  ['spaces/5hp2VyAAAAE', '李姿誼Sica Lee', 'DM'],
  ['spaces/aaa4', '張曼媜', 'DM'],
  ['spaces/aaa5', 'WCS內部小群組', 'GROUP'],
  ['spaces/aaa6', '彰銀Line BC預留 - 9月3日', 'ROOM'],
  ['spaces/aaa7', '碩網團隊ALL', 'ROOM'],
  ['spaces/aaa8', 'P.S.公部門夥伴', 'ROOM'],
  ['spaces/aaa9', 'TPE01P2601 北市府新案', 'ROOM'],
  ['spaces/aa10', '工作紀錄簿', 'ROOM'],
  ['spaces/aa11', '北富銀-內部討論', 'ROOM'],
  ['spaces/aa12', '達駿', 'ROOM'],
].map(([id, displayName, type], i) => ({
  id,
  displayName,
  type,
  threadingState: null,
  lastActiveTime: new Date(Date.now() - (i + 1) * 3600_000).toISOString(),
  memberCount: type === 'DM' ? 2 : 8,
  pinned: false,
  renamable: type === 'DM',
  nameSource: type === 'DM' ? 'dm_peer' : null,
}))

const MENTIONS = [
  {
    id: 101,
    space_id: 'spaces/aaa2',
    space_name: 'ILOOP2601 - 勞動部勞動及職業安全衛生研究所',
    message_name: 'spaces/aaa2/messages/m1',
    thread_name: 'spaces/aaa2/threads/t1',
    sender_display: '林雅娟',
    create_time: new Date(Date.now() - 15 * 3600_000).toISOString(),
    state: 'pending',
    resolved_at: null,
    text: '@鄭浩宇 0831爬蟲測試清單\n可以再麻煩把第一點\n1. 先嘗試看看標準產品能不能爬\n2. 如果不行，是否該網站有其他介接方式 ex. API or CSS',
    has_draft: false,
  },
  {
    id: 102,
    space_id: 'spaces/aaa5',
    space_name: 'WCS內部小群組',
    message_name: 'spaces/aaa5/messages/m2',
    thread_name: 'spaces/aaa5/threads/t2',
    sender_display: '張曼媜',
    create_time: new Date(Date.now() - 26 * 3600_000).toISOString(),
    state: 'pending',
    resolved_at: null,
    text: '@鄭浩宇 請問這一塊 本週可以處理完嗎',
    has_draft: false,
  },
  {
    id: 103,
    space_id: 'spaces/aaa5',
    space_name: 'WCS內部小群組',
    message_name: 'spaces/aaa5/messages/m3',
    thread_name: 'spaces/aaa5/threads/t2',
    sender_display: '林韋澔',
    create_time: new Date(Date.now() - 30 * 3600_000).toISOString(),
    state: 'pending',
    resolved_at: null,
    text: '@鄭浩宇 這是之前煥期船的 給你參考',
    has_draft: false,
  },
]

const ME = {
  viewer: { display_name: '鄭浩宇', email: 'demo@example.com' },
  preferences: { default_limit: 20, default_style: 'technical', default_provider: null },
  mention_counts: { pending: 3, resolved: 28 },
  ai: { providers: [{ id: 'claude', label: 'Claude Code' }], default: 'claude' },
}

const SUMMARY_TEXT = [
  '## 🔧 技術問題與症狀',
  '',
  '本次對話中未出現（僅提及需處理「北市府」相關工作，未描述任何錯誤訊息、發生條件或影響範圍）。',
  '',
  '## ⚠️ 未解的技術問題',
  '',
  '本次對話中未出現技術細節。接手者若要判斷狀況，仍缺少以下資訊：',
  '',
  '- 「北市府」所指的專案／系統與具體處理項目',
  '- 是否有對應的錯誤訊息、環境（正式／測試）與影響範圍',
  '',
  '## 🎯 待辦事項與追蹤 (Action Items)',
  '',
  '• [鄭浩宇] 於 2026-09-09 加班處理北市府相關工作（對話中未說明具體項目）',
  '• [李姿誼Sica Lee] 記錄前述事項（記錄標的內容未在本段對話中出現）',
].join('\n')

const DRAFT_TEXT = [
  '## 脈絡分析',
  '',
  '林雅娟問的是 0831 爬蟲測試清單的第一點：標準產品能不能直接爬。',
  '討論串裡沒有出現先前的測試結果，所以無法判斷「標準產品」指的是哪一版。',
  '',
  '## 建議回話',
  '',
  '雅娟你好，第一點我這邊先確認：標準產品目前可以爬，但有兩個限制要先講清楚。',
  '如果不行的話，我會先看該網站有沒有 API，沒有才考慮 CSS 選擇器那條路。',
].join('\n')

const ev = (o) => `data: ${JSON.stringify(o)}\n\n`

async function main() {
  fs.mkdirSync(OUT, { recursive: true })
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })

  await ctx.route('**/api/v1/**', async (route) => {
    const url = route.request().url()
    const json = (body) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })

    if (url.includes('/auth/status')) return json({ authenticated: true, has_credentials: true })
    // ⚠ 必須用精確比對，不能寫 `url.includes('/me')`：
    // `"/api/v1/mentions".includes('/me')` 是 **true**（`/mentions` 的前三個字元就是 `/me`），
    // 收件匣會拿到使用者物件、清單靜默變空——而且錯得像「沒有待處理的 Mention」這種
    // 看起來完全正常的畫面，最難察覺。
    if (/\/v1\/me(\?|$)/.test(url)) return json(ME)
    if (url.includes('/spaces'))
      return json({
        count: SPACES.length,
        total: 436,
        cached: true,
        cached_at: new Date().toISOString(),
        spaces: SPACES,
      })
    // 排除 `/draft` 與 `stream`：草稿端點是 `/mentions/{id}/draft/stream`，
    // 它也 includes('/mentions')，不排掉就會被這一條攔走、草稿永遠產不出來。
    // （同一個子字串坑本檔踩過兩次，另一次是 `/mentions` 被 `/me` 攔走。）
    if (url.includes('/mentions') && !url.includes('/draft') && !url.includes('stream'))
      return json({
        count: MENTIONS.length,
        counts: { pending: MENTIONS.length, resolved: 28 },
        mentions: MENTIONS,
      })
    if (url.includes('/summaries')) return json({ items: [] })
    if (url.includes('/styles')) return json({ items: [{ id: 'technical', label: '技術細節' }] })
    if (url.includes('/usage')) return json({ items: [], total_input: 0, total_output: 0 })
    if (url.includes('summarize/stream'))
      return route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          ev({
            type: 'meta',
            space: '李姿誼Sica Lee',
            space_id: 'spaces/5hp2VyAAAAE',
            message_count: 3,
            image_count: 0,
            style: 'technical',
            provider: 'claude',
            model: 'claude-cli:opus',
          }) +
          ev({ type: 'chunk', text: SUMMARY_TEXT }) +
          ev({ type: 'done', summary_id: 1 }),
      })
    if (url.includes('draft') && url.includes('stream'))
      return route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          ev({
            type: 'meta',
            space: 'ILOOP2601 - 勞動部勞動及職業安全衛生研究所',
            mention_id: 101,
            message_count: 18,
            image_count: 0,
            provider: 'claude',
            model: 'claude-cli:opus',
          }) +
          ev({ type: 'chunk', text: DRAFT_TEXT }) +
          ev({ type: 'done' }),
      })
    // Catch-all：把所有可能的集合欄位都給成空陣列。
    //
    // **不可以只回 `{ items: [] }`**：store 的 load() 是 `set({ polishers: res.polishers })`
    // 這種取法，欄位名對不上就會把 undefined 塞進去，而 `sepiaAvailability()` 對它呼叫
    // `.find()` 直接讓整個 React 樹崩潰——設定頁白畫面，連帶後面所有操作都選不到元素。
    // store 的初始值本來是 `[]`，是 API 回應把它蓋成 undefined 的。
    return json({
      items: [],
      tones: [{ id: 'direct', label: '直接', description: '先講結論，再補必要的細節。', example: '這個問題出在 retryCount 沒有重設，我今天會修。' }],
      personas: [],
      prompts: [],
      reply_prompts: [],
      polishers: [{ name: 'sepia', label: 'Sepia', available: true, version: 'v0.8.0' }],
      projects: [],
      code_projects: [],
    })
  })

  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))

  const results = []
  // 開場先確認實際主題——index.html 寫死 class="dark"，若這裡量到 light
  // 表示 next-themes 在 hydration 時覆寫了，那是要知道的事實不是可以忽略的細節
  const shoot = async (tag) => {
    await page.waitForTimeout(600)
    await page.screenshot({ path: path.join(OUT, `${tag}.png`) })
    const probe = await page.evaluate(() => {
      const de = document.documentElement
      return de.scrollWidth > de.clientWidth ? `${de.scrollWidth}>${de.clientWidth}` : null
    })
    results.push(`${tag}: ${probe ? '⚠ 水平溢出 ' + probe : '無溢出'}`)
  }

  const themeOf = () =>
    page.evaluate(() => (document.documentElement.className.includes('dark') ? 'dark' : 'light'))

  for (let round = 0; round < 2; round++) {
    // ── 摘要工作台（含產生一次摘要）──
    await page.goto(BASE + '/index.html#/summary', { waitUntil: 'load' })
    await page.waitForTimeout(1800)
    // ⚠ **主題一定要在頁面載入之後才讀**。page 剛建立時是 about:blank，
    // html 上沒有任何 class，themeOf() 會回 'light' 而實際是 dark
    // （index.html 寫死 class="dark"）。時機錯了的後果不是少一張圖——
    // 第一輪的深色被標成 -light，第二輪真的 light 時檔名撞上直接覆蓋，
    // 最後八張全是淺色，看起來像「深色沒截到」。
    const theme = await themeOf()
    const space = page
      .locator('[role="listbox"][aria-label="要做摘要的 Space"] [role="option"]')
      .first()
    if (await space.count()) {
      await space.click()
      await page.waitForTimeout(500)
    }
    const start = page.getByRole('button', { name: /開始摘要|重新摘要/ })
    if ((await start.count()) && (await start.first().isEnabled())) {
      await start.first().click()
      await page.waitForTimeout(1200)
    }
    await shoot(`summary-${theme}`)

    // ── Mention 收件匣 ──
    await page.goto(BASE + '/index.html#/mentions', { waitUntil: 'load' })
    await page.waitForTimeout(1600)
    await shoot(`inbox-${theme}`)

    // ── 草稿工作區（點第一則，產生一次草稿）──
    const card = page.locator('[data-mention-index]').first()
    if (await card.count()) {
      await card.click()
      await page.waitForTimeout(900)
      const gen = page.getByRole('button', { name: /產生 Draft Reply|重新產生/ })
      if ((await gen.count()) && (await gen.first().isEnabled())) {
        await gen.first().click()
        await page.waitForTimeout(1500)
      }
    }
    await shoot(`draft-${theme}`)

    // ── 設定中心 ──
    await page.goto(BASE + '/index.html#/settings/reply', { waitUntil: 'load' })
    await page.waitForTimeout(1600)
    await shoot(`settings-${theme}`)

    if (round === 0) {
      // 切主題：點導覽底部那顆，不要硬改 class
      await page.goto(BASE + '/index.html#/summary', { waitUntil: 'load' })
      await page.waitForTimeout(1200)
      // 主題鈕在左側導覽底部。點它自己的按鈕，不要硬改 class——那會繞過
      // next-themes 的狀態，依賴 JS 狀態的東西不會更新，截出來像元件有 bug。
      const toggle = page.locator('nav[aria-label="主導覽"] button[aria-label*="切換為"]').first()
      if (!(await toggle.count())) {
        results.push('⚠ 找不到主題切換鈕')
        break
      }
      const before = await themeOf()
      await toggle.click()
      // next-themes 改 class 是非同步的，輪詢等它，不要用固定 timeout 賭
      try {
        await page.waitForFunction(
          (prev) =>
            (document.documentElement.className.includes('dark') ? 'dark' : 'light') !== prev,
          before,
          { timeout: 4000 },
        )
      } catch {
        const cls = await page.evaluate(() => document.documentElement.className)
        results.push(`⚠ 點了主題鈕但主題沒變（html class="${cls}"）`)
        break
      }
    }
  }

  console.log(results.join('\n'))
  console.log(errors.length ? `\n⚠ pageerror ${errors.length}: ${errors[0]}` : '\n無 pageerror')
  console.log('輸出：' + OUT)
  await browser.close()
}

main().catch((e) => {
  console.error('失敗:', e)
  process.exit(1)
})
