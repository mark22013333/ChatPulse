const fs = require('node:fs')
const path = require('node:path')
const http = require('node:http')
const { execFileSync } = require('node:child_process')
const globalRoot = execFileSync('npm', ['root', '-g'], { encoding: 'utf8' }).trim()
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || path.join(globalRoot, 'playwright'))
const outputArg = process.argv.indexOf('--output-dir')
const output = outputArg >= 0 ? path.resolve(process.argv[outputArg + 1]) : '/tmp/chatpulse-reply-evidence'
const recordOnly = process.argv.includes('--record-only')
const receipt = { mode: recordOnly ? 'record' : 'verify', requests: [], checks: [], unknown: [], errors: [] }
const viewer = { id: 1, google_user_id: 'users/fixture', email: 'viewer@example.test', display_name: '合成 Viewer' }
const mentions = [7, 8].map((id) => ({ id, space_id: 'spaces/question', space_name: '合成問題 Space', message_name: `spaces/question/messages/${id}`, thread_name: `spaces/question/threads/${id}`, sender_display: `合成提問者 ${id}`, create_time: '2026-01-02T00:00:00Z', state: 'pending', resolved_at: null, has_draft: false, text: `合成問題 ${id}` }))
const preferences = { project_context_opt_in: true, pinned_space_ids: [], default_limit: 50, default_style: 'general', default_provider: null, default_models: {}, default_reply_tone: null, default_persona_id: null, default_reply_prompt_id: null, default_sepia_enabled: null }
const ai = { default: 'fake', providers: [{ name: 'fake', label: '合成 AI', available: true, model: 'fake', models: ['fake'], supports_vision: false }] }
const replies = {
  '/auth/status': { authenticated: true, viewer, legacy_token_available: false, can_bootstrap: false, viewer_count: 1 },
  '/me': { features: { project_context: true, reply_evidence: true }, viewer, scopes: [], has_identity_scope: true, preferences, collector: null, mention_counts: { pending: 2, resolved: 0 }, ai },
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
const profile = { id: 4, revision: 2, name: '合成查核專案', sources: [
  { id: 'private', kind: 'chat', label: '合成私有 PM', category: 'pm', space_id: 'spaces/private' },
  { id: 'public', kind: 'chat', label: '合成部署回報', category: 'space', space_id: 'spaces/public' },
  { id: 'metadata', kind: 'chat', label: '合成待選來源', category: 'space', space_id: 'spaces/metadata' },
] }
replies['/project-profiles'] = { profiles: [profile] }
const digest = 'synthetic-scope-digest'
const scope = { schema_version: 1, owner_viewer_id: 1, mention_id: 7,
  message_name: mentions[0].message_name, target_space_id: mentions[0].space_id,
  target_thread_name: mentions[0].thread_name, profile_id: profile.id,
  profile_name: profile.name, revision: profile.revision, environment: 'uat',
  sources: profile.sources.map((source, index) => ({ ...source, use: ['analysis_only', 'reply_context', 'metadata_only'][index] })),
  limit: 50, code_terms: [], digest,
}
function assessment(text, supported = true) {
  return { status: 'evaluated', method: 'model_assisted_v1',
    text_hash: require('node:crypto').createHash('sha256').update(text).digest('hex'),
    claims: [{ id: 'c1', text, kind: 'status', relation: supported ? 'supported' : 'insufficient',
      reference_validity: 'valid', currency: 'current', scope: 'match', reason: '合成來源回報，僅測操作流程。',
      citations: [{ evidence_id: 'e1', locator: 'spaces/public/messages/1', revision: 'synthetic-revision' }] }],
    gaps: supported ? [] : ['缺少部署證據'], stop_reason: null }
}
function check(name, passed, actual) { receipt.checks.push({ name, passed, actual }); if (!passed && !recordOnly) throw new Error(name) }
async function main() {
  fs.mkdirSync(output, { recursive: true })
  let server
  let browser
  const pending = new Map()
  let ready
  const streamReady = new Promise((resolve) => { ready = resolve })
  let reviews = 0
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
        return new Promise((resolve) => {
          pending.set(id, async () => {
            const text = 'UAT 已部署版本 7。'
            const events = [
              { type: 'meta', mention_id: id, provider: 'fake', project_scope: scope, scope_digest: digest,
                answering: [{ mention_id: id }], reference_spaces: [], code_refs: [] },
              { type: 'review_progress', stage: 'reviewing', mention_id: id, scope_digest: digest, counts: { reads: 3 } },
              { type: 'chunk', text: 'SYNTHETIC_PRIVATE_ANALYSIS_MUST_NOT_RENDER' },
              { type: 'done', draft_id: 107, reply: text, scope_digest: digest, project_status: 'complete',
                assessment: assessment(text), receipt_id: 'synthetic-receipt-1', review_revision: 1 },
            ]
            await route.fulfill({ contentType: 'text/event-stream', body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('') })
            resolve()
          })
          ready()
        })
      }
      if (request.method() === 'POST' && /^\/mentions\/\d+\/draft\/review$/.test(endpoint)) {
        reviews += 1
        const supported = reviews > 1
        return route.fulfill({ json: { draft_id: 107, text_hash: assessment(body.text).text_hash,
          review_revision: reviews + 1, receipt_id: supported ? 'synthetic-receipt-3' : null,
          assessment: assessment(body.text, supported) } })
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
    await page.getByRole('combobox', { name: '本題專案', exact: true }).selectOption('4')
    await page.getByRole('combobox', { name: '本題環境', exact: true }).selectOption('uat')
    await page.getByRole('combobox', { name: '合成私有 PM 用途', exact: true }).selectOption('analysis_only')
    await page.getByRole('combobox', { name: '合成部署回報 用途', exact: true }).selectOption('reply_context')
    await page.getByRole('checkbox', { name: '確認本題確切目標', exact: true }).check()
    await page.screenshot({ path: path.join(output, 'selected-sources.png'), fullPage: true, animations: 'disabled' })
    await page.getByRole('button', { name: '產生 Draft Reply', exact: true }).click()
    let streamTimer
    try {
      await Promise.race([streamReady, new Promise((_, reject) => {
        streamTimer = setTimeout(() => reject(new Error('等待假生成請求逾時')), 12000)
      })])
    } finally { clearTimeout(streamTimer) }
    const generated = receipt.requests.find((request) => request.endpoint === '/mentions/7/draft/stream')
    check('請求保留三態用途及 UAT 環境', generated?.body.project_selection.environment === 'uat'
      && JSON.stringify(generated.body.project_selection.sources) === JSON.stringify([
        { binding_id: 'private', use: 'analysis_only' }, { binding_id: 'public', use: 'reply_context' },
        { binding_id: 'metadata', use: 'metadata_only' },
      ]), generated?.body)
    check('請求綁定確切討論串', generated?.body.project_selection.target_thread_name === mentions[0].thread_name)
    await pending.get(7)()
    const editor = page.getByPlaceholder('建議回話會串流到這裡，你可以直接修改。')
    await editor.waitFor()
    await page.waitForFunction(() => document.querySelector('textarea[placeholder="建議回話會串流到這裡，你可以直接修改。"]')?.value === 'UAT 已部署版本 7。')
    const send = page.getByRole('button', { name: '送出回話', exact: true })
    check('生成完成且有收據可確認送出', await send.isEnabled())
    check('私有串流片段不顯示', !(await page.locator('body').innerText()).includes('SYNTHETIC_PRIVATE_ANALYSIS_MUST_NOT_RENDER'))
    check('顯示模型輔助而非已證實', await page.getByRole('region', { name: '回話評估' }).getByText('模型輔助評估，仍需確認', { exact: true }).isVisible())
    await editor.fill('人工版本：正式環境已部署。')
    check('人工改字立即禁止送出', await send.isDisabled())
    await page.getByRole('button', { name: '查核目前回話', exact: true }).click()
    await page.getByText('支持不足', { exact: false }).waitFor()
    check('不通過的重查維持禁止送出', await send.isDisabled())
    check('重查保留人工原文', await editor.inputValue() === '人工版本：正式環境已部署。')
    const exactText = '人工確認：UAT 已部署版本 7。\n請協助驗收。'
    await editor.fill(exactText)
    await page.getByRole('button', { name: '查核目前回話', exact: true }).click()
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some((button) => button.textContent === '送出回話' && !button.disabled))
    const review = receipt.requests.filter((request) => request.endpoint.endsWith('/draft/review'))
    check('重查送出原樣文字及既有 scope', review.length === 2 && review[1].body.text === exactText && review[1].body.scope_digest === digest, review)
    await send.click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('button', { name: '確認送出', exact: true }).waitFor()
    check('二次確認前沒有送出', receipt.requests.every((request) => !request.endpoint.endsWith('/reply')))
    await page.screenshot({ path: path.join(output, 'review-confirm.png'), fullPage: true, animations: 'disabled' })
    await dialog.getByRole('button', { name: '確認送出', exact: true }).click()
    await dialog.waitFor({ state: 'hidden' })
    const sent = receipt.requests.filter((request) => request.endpoint.endsWith('/reply'))
    check('只送出一次原文與最新收據', sent.length === 1 && sent[0].body.text === exactText
      && sent[0].body.receipt_id === 'synthetic-receipt-3' && sent[0].body.review_revision === 3, sent)
    check('沒有未知 API 或外部請求', receipt.unknown.length === 0, receipt.unknown)
    check('沒有前端執行錯誤', receipt.errors.length === 0, receipt.errors)
    await page.screenshot({ path: path.join(output, 'sent.png'), fullPage: true, animations: 'disabled' })
  } catch (error) {
    receipt.errors.push(error.message)
    if (browser) { const pages = browser.contexts()[0]?.pages(); if (pages?.[0]) await pages[0].screenshot({ path: path.join(output, 'failure.png'), fullPage: true, animations: 'disabled' }).catch(() => {}) }
  } finally {
    if (browser) await browser.close()
    if (server) await new Promise((resolve) => server.close(resolve))
    fs.writeFileSync(path.join(output, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n')
  }
  const passed = receipt.errors.length === 0 && receipt.unknown.length === 0 && receipt.checks.length >= 13 && receipt.checks.every((item) => item.passed)
  console.log(JSON.stringify({ passed, output, checks: receipt.checks, unknown: receipt.unknown, errors: receipt.errors }, null, 2))
  process.exitCode = passed ? 0 : 1
}
main().catch((error) => { console.error(error); process.exitCode = 1 })
