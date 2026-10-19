const fs = require('node:fs')
const path = require('node:path')
const http = require('node:http')
const { execFileSync } = require('node:child_process')
const globalRoot = execFileSync('npm', ['root', '-g'], { encoding: 'utf8' }).trim()
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || path.join(globalRoot, 'playwright'))
const outputArg = process.argv.indexOf('--output-dir')
const output = outputArg >= 0 ? path.resolve(process.argv[outputArg + 1]) : '/tmp/chatpulse-reply-scope'
const recordOnly = process.argv.includes('--record-only')
const receipt = { mode: recordOnly ? 'record' : 'verify', requests: [], checks: [], unknown: [], errors: [] }
const viewer = { id: 1, google_user_id: 'users/fixture', email: 'viewer@example.test', display_name: '合成 Viewer' }
const mentions = [7, 8].map((id) => ({ id, space_id: 'spaces/question', space_name: '合成問題 Space', message_name: `spaces/question/messages/${id}`, thread_name: `spaces/question/threads/${id}`, sender_display: `合成提問者 ${id}`, create_time: '2026-01-02T00:00:00Z', state: 'pending', resolved_at: null, has_draft: false, text: `合成問題 ${id}` }))
const preferences = { pinned_space_ids: [], default_limit: 50, default_style: 'general', default_provider: null, default_models: {}, default_reply_tone: null, default_persona_id: null, default_reply_prompt_id: null, default_sepia_enabled: null }
const ai = { default: 'fake', providers: [{ name: 'fake', label: '合成 AI', available: true, model: 'fake', models: ['fake'], supports_vision: false }] }
const replies = {
  '/auth/status': { authenticated: true, viewer, legacy_token_available: false, can_bootstrap: false, viewer_count: 1 },
  '/me': { viewer, scopes: [], has_identity_scope: true, preferences, collector: null, mention_counts: { pending: 2, resolved: 0 }, ai },
  '/providers': ai,
  '/styles': { default: 'general', styles: [{ value: 'general', label: '一般', description: '' }] },
  '/summaries': { count: 0, summaries: [] },
  '/spaces': { count: 1, total: 1, spaces: [{ id: 'spaces/reference', displayName: '合成 Reference Space', type: 'SPACE', threadingState: 'THREADED_MESSAGES', lastActiveTime: null }] },
  '/mentions': { count: 2, counts: { pending: 2, resolved: 0 }, mentions },
  '/reply-tones': { default: 'direct', tones: [{ id: 'direct', label: '自然直接', description: '' }] },
  '/personas': { personas: [], sources: [] },
  '/reply-prompts': { reply_prompts: [] },
  '/polishers': { polishers: [], sepia: { available: false } },
  '/code-projects': { projects: [{ id: 3, name: '合成專案', branches: { production: 'main', uat: 'uat' }, default_env: 'production', enabled: true, include_globs: [], exclude_globs: [] }] },
}
function check(name, passed, actual) { receipt.checks.push({ name, passed, actual }); if (!passed && !recordOnly) throw new Error(name) }
async function main() {
  fs.mkdirSync(output, { recursive: true })
  let server
  let browser
  const pending = new Map()
  try {
    let base = process.env.CHATPULSE_URL
    if (!base) {
      const dist = path.resolve(__dirname, '../../dashboard/frontend/dist')
      if (!fs.existsSync(path.join(dist, 'index.html'))) throw new Error('請先建置 production dist')
      server = http.createServer((req, res) => {
        const name = decodeURIComponent(new URL(req.url, 'http://localhost').pathname)
        const file = path.resolve(dist, '.' + (name === '/' ? '/index.html' : name))
        if (!file.startsWith(dist + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404).end(); return }
        const mime = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' }
        res.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream' })
        fs.createReadStream(file).pipe(res)
      })
      await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
      base = `http://127.0.0.1:${server.address().port}`
    }
    receipt.base = base
    browser = await chromium.launch({ headless: !process.argv.includes('--headed') })
    const context = await browser.newContext({ viewport: { width: 1600, height: 1100 }, serviceWorkers: 'block' })
    await context.route('**/*', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      const local = new URL(base)
      if (url.origin === local.origin && !url.pathname.startsWith('/api/')) {
        if (request.method() === 'GET' && (url.pathname === '/' || url.pathname === '/index.html' || url.pathname.startsWith('/assets/'))) return route.continue()
      }
      if (url.origin !== local.origin || !url.pathname.startsWith('/api/v1/')) {
        receipt.unknown.push({ method: request.method(), url: request.url() }); return route.abort('blockedbyclient')
      }
      const endpoint = url.pathname.slice('/api/v1'.length)
      const body = request.postDataJSON()
      receipt.requests.push({ method: request.method(), endpoint, body })
      if (request.method() === 'GET' && Object.hasOwn(replies, endpoint)) return route.fulfill({ json: replies[endpoint] })
      if (request.method() === 'GET' && /^\/mentions\/\d+\/draft$/.test(endpoint)) return route.fulfill({ status: 404, json: { error: { code: 'DRAFT_NOT_FOUND', message: '合成 Mention 尚無草稿' } } })
      if (request.method() === 'POST' && /^\/mentions\/\d+\/draft\/stream$/.test(endpoint)) {
        const id = Number(endpoint.split('/')[2])
        return new Promise((resolve) => pending.set(id, async () => {
          const events = [{ type: 'meta', mention_id: id, provider: 'fake', answering: [{ mention_id: id }] }, { type: 'chunk', text: `### ✍️ 建議回話\n合成回話 ${id}` }, { type: 'done', draft_id: 100 + id, reply: `合成回話 ${id}` }]
          await route.fulfill({ contentType: 'text/event-stream', body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('') }).catch(() => {})
          resolve()
        }))
      }
      if (request.method() === 'POST' && /^\/mentions\/\d+\/reply$/.test(endpoint)) {
        const id = Number(endpoint.split('/')[2])
        const mention = { ...mentions.find((item) => item.id === id), state: 'resolved', resolved_at: '2026-01-03T00:00:00Z' }
        return route.fulfill({ json: { mention, mentions: [mention], message_name: `spaces/question/messages/sent-${id}` } })
      }
      receipt.unknown.push({ method: request.method(), endpoint }); return route.abort('blockedbyclient')
    })
    const page = await context.newPage()
    page.on('pageerror', (error) => receipt.errors.push(error.message))
    page.setDefaultTimeout(12000)
    await page.goto(`${base}/#/mentions/7`)
    await page.getByRole('button', { name: '產生 Draft Reply', exact: true }).waitFor()
    await page.getByRole('listbox', { name: '一起當作參考的 Space' }).getByRole('option').click()
    await page.locator('label').filter({ hasText: '正式' }).click()
    await page.locator('#draft-code-terms').fill('known_symbol')
    await page.locator('#draft-limit').fill('77')
    await page.locator('#draft-custom-prompt').fill('僅使用已知證據')
    await page.screenshot({ path: path.join(output, 'a-selected.png'), fullPage: true, animations: 'disabled' })
    await page.evaluate(() => { window.location.hash = '#/mentions/8' })
    await page.locator('#main').getByText('合成問題 8', { exact: true }).waitFor()
    await page.getByRole('button', { name: '產生 Draft Reply', exact: true }).click()
    await page.waitForFunction(() => document.querySelector('button') && [...document.querySelectorAll('button')].some((button) => button.textContent.includes('停止串流')))
    const generated = receipt.requests.filter((r) => r.endpoint === '/mentions/8/draft/stream')
    check('B request 不沿用 A Reference Space', generated[0]?.body.reference_space_ids.length === 0, generated[0]?.body)
    check('B request 不沿用 A code refs／terms', generated[0]?.body.code_refs.length === 0 && !Object.hasOwn(generated[0].body, 'code_terms'), generated[0]?.body)
    check('跨 Mention 保留抓取則數與自訂提示', generated[0]?.body.limit === 77 && generated[0]?.body.custom_prompt === '僅使用已知證據', generated[0]?.body)
    await page.locator('a[href^="#/summary"]').first().click()
    await page.locator('a[href^="#/mentions"]').first().click()
    check('同題切頁籤仍生成中', await page.getByRole('button', { name: '停止串流', exact: true }).isVisible(), page.url())
    check('同題切頁籤未重發生成', receipt.requests.filter((r) => r.endpoint === '/mentions/8/draft/stream').length === 1)
    await pending.get(8)()
    await page.getByRole('button', { name: '送出回話', exact: true }).waitFor({ state: 'visible' })
    await page.getByRole('button', { name: '送出回話', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('button', { name: '確認送出', exact: true }).waitFor()
    check('確認前零送出', receipt.requests.every((r) => !r.endpoint.endsWith('/reply')))
    await page.screenshot({ path: path.join(output, 'b-confirm.png'), fullPage: true, animations: 'disabled' })
    await dialog.getByRole('button', { name: '確認送出', exact: true }).click()
    await dialog.waitFor({ state: 'hidden' })
    const sent = receipt.requests.filter((r) => r.endpoint.endsWith('/reply'))
    check('二次確認只 mock 送出 B', sent.length === 1 && sent[0].endpoint === '/mentions/8/reply' && sent[0].body.text === '合成回話 8', sent)
    check('沒有未知 API 或外部請求', receipt.unknown.length === 0, receipt.unknown)
    check('沒有前端執行錯誤', receipt.errors.length === 0, receipt.errors)
    await page.screenshot({ path: path.join(output, 'b-sent.png'), fullPage: true, animations: 'disabled' })
  } catch (error) {
    receipt.errors.push(error.message)
    if (browser) { const pages = browser.contexts()[0]?.pages(); if (pages?.[0]) await pages[0].screenshot({ path: path.join(output, 'failure.png'), fullPage: true, animations: 'disabled' }).catch(() => {}) }
  } finally {
    if (browser) await browser.close()
    if (server) await new Promise((resolve) => server.close(resolve))
    fs.writeFileSync(path.join(output, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n')
  }
  const passed = receipt.errors.length === 0 && receipt.unknown.length === 0 && receipt.checks.length >= 8 && receipt.checks.every((item) => item.passed)
  console.log(JSON.stringify({ passed, output, checks: receipt.checks, unknown: receipt.unknown, errors: receipt.errors }, null, 2))
  process.exitCode = passed ? 0 : 1
}
main().catch((error) => { console.error(error); process.exitCode = 1 })
