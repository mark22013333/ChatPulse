const fs = require('node:fs')
const path = require('node:path')
const http = require('node:http')
const { execFileSync } = require('node:child_process')
const globalRoot = execFileSync('npm', ['root', '-g'], { encoding: 'utf8' }).trim()
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || path.join(globalRoot, 'playwright'))
const outputArg = process.argv.indexOf('--output-dir')
const output = outputArg >= 0 ? path.resolve(process.argv[outputArg + 1]) : path.resolve(__dirname, '../../.audit/pr03-owner/ui')
const recordOnly = false
const receipt = { mode: recordOnly ? 'record' : 'verify', requests: [], checks: [], unknown: [], errors: [] }
const viewer = { id: 1, google_user_id: 'users/fixture', email: 'viewer@example.test', display_name: '合成 Viewer' }
const mentions = [7, 8].map((id) => ({ id, space_id: 'spaces/question', space_name: '合成問題 Space', message_name: `spaces/question/messages/${id}`, thread_name: `spaces/question/threads/${id}`, sender_display: `合成提問者 ${id}`, create_time: '2026-01-02T00:00:00Z', state: 'pending', resolved_at: null, has_draft: false, text: `合成問題 ${id}` }))
const preferences = { pinned_space_ids: [], default_limit: 50, default_style: 'general', default_provider: null, default_models: {}, default_reply_tone: null, default_persona_id: null, default_reply_prompt_id: null, default_sepia_enabled: null, project_context_opt_in: false }
const ai = { default: 'fake', providers: [{ name: 'fake', label: '合成 AI', available: true, model: 'fake', models: ['fake'], supports_vision: false }] }
const replies = {
  '/auth/status': { authenticated: true, viewer, legacy_token_available: false, can_bootstrap: false, viewer_count: 1 },
  '/me': { features: { project_context: true }, viewer, scopes: [], has_identity_scope: true, preferences, collector: null, mention_counts: { pending: 2, resolved: 0 }, ai },
  '/providers': ai,
  '/styles': { default: 'general', styles: [{ value: 'general', label: '一般', description: '' }] },
  '/summaries': { count: 0, summaries: [] },
  '/spaces': { count: 3, total: 3, spaces: [
    { id: 'spaces/pm', displayName: '合成 PM', type: 'DIRECT_MESSAGE', threadingState: 'THREADED_MESSAGES' },
    { id: 'spaces/team', displayName: '合成工程 Space', type: 'SPACE', threadingState: 'THREADED_MESSAGES' },
    { id: 'spaces/release', displayName: '合成發布 Space', type: 'SPACE', threadingState: 'THREADED_MESSAGES' },
  ] },
  '/mentions': { count: 2, counts: { pending: 2, resolved: 0 }, mentions },
  '/reply-tones': { default: 'direct', tones: [{ id: 'direct', label: '自然直接', description: '' }] },
  '/personas': { personas: [], sources: [] },
  '/reply-prompts': { reply_prompts: [] },
  '/polishers': { polishers: [], sepia: { available: false } },
  '/code-projects': { projects: [{ id: 3, name: '合成專案', branches: { production: 'main', uat: 'uat' }, default_env: 'production', enabled: true, include_globs: [], exclude_globs: [] }] },
}
const profiles = [{ id: 2, name: '乙案', revision: 1, sources: [
  { id: 'pm-b', kind: 'chat', label: '合成 PM', space_id: 'spaces/pm', category: 'pm' },
] }]
const stored = new Map()
let stale = false
let holdNext = false
let delayed = null
function check(name, passed, actual) {
  receipt.checks.push({ name, passed, actual })
  if (!passed) throw new Error(name)
}
async function main() {
  fs.mkdirSync(output, { recursive: true })
  let server
  let browser
  try {
    const dist = path.resolve(__dirname, '../../dashboard/frontend/dist')
    server = http.createServer((req, res) => {
      const name = decodeURIComponent(new URL(req.url, 'http://localhost').pathname)
      const file = path.resolve(dist, '.' + (name === '/' ? '/index.html' : name))
      if (!file.startsWith(dist + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404).end(); return }
      const mime = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' }
      res.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream' })
      fs.createReadStream(file).pipe(res)
    })
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
    const base = `http://127.0.0.1:${server.address().port}`
    receipt.base = base
    browser = await chromium.launch({ headless: true })
    const context = await browser.newContext({
      viewport: { width: 1600, height: 1100 }, serviceWorkers: 'block',
      recordVideo: { dir: path.join(output, 'video'), size: { width: 1280, height: 880 } },
    })
    await context.route('**/*', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      if (url.origin === base && request.method() === 'GET' && (url.pathname === '/' || url.pathname === '/index.html' || url.pathname.startsWith('/assets/'))) return route.continue()
      if (url.origin !== base || !url.pathname.startsWith('/api/v1/')) {
        receipt.unknown.push({ method: request.method(), url: request.url() })
        return route.abort('blockedbyclient')
      }
      const endpoint = url.pathname.slice('/api/v1'.length)
      const body = request.postDataJSON()
      receipt.requests.push({ method: request.method(), endpoint, body })
      if (request.method() === 'PATCH' && endpoint === '/preferences/project-context') {
        preferences.project_context_opt_in = body.enabled
        return route.fulfill({ json: preferences })
      }
      if (endpoint === '/project-profiles' && request.method() === 'GET') return route.fulfill({ json: { profiles } })
      if (endpoint === '/project-profiles' && request.method() === 'POST') {
        const profile = { id: 3, revision: 1, ...body }
        profiles.push(profile)
        return route.fulfill({ json: profile })
      }
      if (request.method() === 'PUT' && endpoint.startsWith('/project-profiles/')) {
        const profile = profiles.find((p) => p.id === Number(endpoint.split('/')[2]))
        Object.assign(profile, body, { revision: profile.revision + 1 })
        return route.fulfill({ json: profile })
      }
      if (request.method() === 'GET' && Object.hasOwn(replies, endpoint)) return route.fulfill({ json: replies[endpoint] })
      if (request.method() === 'GET' && /^\/mentions\/\d+\/draft$/.test(endpoint)) {
        const id = Number(endpoint.split('/')[2])
        return stored.has(id)
          ? route.fulfill({ json: { ...stored.get(id), scope_state: stale ? 'stale' : 'current' } })
          : route.fulfill({ status: 404, json: { error: { code: 'DRAFT_NOT_FOUND', message: '合成草稿不存在' } } })
      }
      if (request.method() === 'POST' && /^\/mentions\/\d+\/draft\/stream$/.test(endpoint)) {
        const id = Number(endpoint.split('/')[2])
        const selection = body.project_selection
        const profile = profiles.find((p) => p.id === selection.profile_id)
        const scope = {
          schema_version: 1, owner_viewer_id: 1, mention_id: id,
          message_name: `spaces/question/messages/${id}`,
          target_space_id: selection.target_space_id, target_thread_name: selection.target_thread_name,
          profile_id: profile.id, profile_name: profile.name, revision: profile.revision,
          sources: profile.sources.map((source) => ({ ...source, use: selection.sources.find((c) => c.binding_id === source.id).use })),
          limit: body.limit, code_terms: body.code_terms || [], digest: `fake-digest-${id}-${profile.id}`,
        }
        const content = `### 🧭 脈絡分析\n合成分析 ${profile.name}\n### ✍️ 建議回話\n合成回話 ${profile.name}`
        const meta = {
          type: 'meta', mention_id: id, project_scope: scope, semantic_status: 'unverified',
          provider: 'fake', answering: [{ mention_id: id }],
          reference_spaces: scope.sources.filter((s) => s.kind === 'chat' && s.use === 'reply_context').map((s) => ({ space_id: s.space_id, space_name: s.label, message_count: 1 })),
          code_refs: [], context: { mode: 'thread', coverage: 'full', message_count: 1 },
        }
        const finish = async () => {
          stored.set(id, { draft_id: 100 + id, mention_id: id, content_md: content,
            generation_config: { meta, project_status: 'complete', semantic_status: 'unverified' },
            created_at: '2026-10-20T00:00:00Z', sent_at: null })
          const events = [meta, { type: 'chunk', text: content }, { type: 'done', draft_id: 100 + id, scope_digest: scope.digest, project_status: 'complete', reply: `合成回話 ${profile.name}` }]
          await route.fulfill({ contentType: 'text/event-stream', body: events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('') }).catch(() => {})
        }
        if (holdNext) {
          holdNext = false
          return new Promise((resolve) => { delayed = async () => { await finish(); resolve() } })
        }
        return finish()
      }
      if (request.method() === 'POST' && /^\/mentions\/\d+\/reply$/.test(endpoint)) {
        if (stale) return route.fulfill({ status: 409, json: { error: { code: 'PROJECT_CONTEXT_STALE', message: '合成專案已更新，請重新產生' } } })
        const id = Number(endpoint.split('/')[2])
        const mention = { ...mentions.find((m) => m.id === id), state: 'resolved' }
        return route.fulfill({ json: { status: 'sent', mention, mentions: [mention] } })
      }
      receipt.unknown.push({ method: request.method(), endpoint })
      return route.abort('blockedbyclient')
    })
    const page = await context.newPage()
    page.on('pageerror', (error) => receipt.errors.push(error.message))
    page.setDefaultTimeout(12000)
    await page.goto(`${base}/#/mentions/7`)
    const panel = page.getByRole('region', { name: '專案脈絡', exact: true })
    await panel.getByRole('checkbox', { name: '啟用我的專案脈絡' }).click()
    await panel.getByLabel('本題專案', { exact: true }).waitFor()
    await panel.getByText('管理專案來源', { exact: true }).click()
    await panel.getByLabel('專案名稱', { exact: true }).fill('甲案')
    for (const sid of ['spaces/pm', 'spaces/team', 'spaces/release']) {
      await panel.getByLabel('新增 Space 來源', { exact: true }).selectOption(sid)
    }
    await panel.getByLabel('合成 PM 類別', { exact: true }).selectOption('pm')
    await panel.getByRole('button', { name: '加入 合成專案 · 正式環境', exact: true }).click()
    await panel.getByLabel('合成專案 相對路徑', { exact: true }).fill('src/main.py')
    await panel.getByRole('button', { name: '保存新專案', exact: true }).click()
    await page.getByRole('option', { name: '甲案 · r1', exact: true }).waitFor({ state: 'attached' })
    check('保存多來源 profile', profiles.find((p) => p.name === '甲案')?.sources.length === 4, profiles)
    await page.screenshot({ path: path.join(output, '01-profile-saved.png'), fullPage: true, animations: 'disabled' })
    await page.reload()
    await panel.getByLabel('本題專案', { exact: true }).selectOption('3')
    const sourceChecks = panel.locator('label').filter({ hasText: '合成 PM · PM' }).getByRole('checkbox')
    check('重載明選專案仍預設零核准與零同意', !await sourceChecks.isChecked() && !await panel.getByLabel('確認本題確切目標', { exact: true }).isChecked())
    await sourceChecks.check()
    await panel.getByLabel('確認本題確切目標', { exact: true }).check()
    await page.getByRole('button', { name: '產生 Draft Reply', exact: true }).click()
    await page.getByRole('region', { name: '本次專案範圍' }).getByText('甲案 · r1 · 未語意查證', { exact: true }).waitFor()
    const generated = receipt.requests.find((r) => r.endpoint === '/mentions/7/draft/stream')
    check('每題核准只有 PM 且無 legacy 來源混帶', generated.body.project_selection.sources.filter((s) => s.use === 'reply_context').length === 1 && !Object.hasOwn(generated.body, 'reference_space_ids') && !Object.hasOwn(generated.body, 'code_refs'), generated.body)
    await page.getByRole('button', { name: '送出回話', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByText(/甲案 · r1 · 未語意查證/).waitFor()
    check('確認採 snapshot 與精確 target', (await dialog.innerText()).includes('spaces/question/threads/7'))
    check('二次確認前零送出', !receipt.requests.some((r) => r.endpoint.endsWith('/reply')))
    await page.screenshot({ path: path.join(output, '02-snapshot-confirm.png'), fullPage: true, animations: 'disabled' })
    stale = true
    await dialog.getByRole('button', { name: '確認送出', exact: true }).click()
    await page.getByText(/合成專案已更新，請重新產生/).first().waitFor()
    check('過期送出回 409，確認框維持開啟', await dialog.isVisible())
    await page.screenshot({ path: path.join(output, '03-stale-rejected.png'), fullPage: true, animations: 'disabled' })
    await page.keyboard.press('Escape')
    await page.reload()
    const sendButton = page.getByRole('button', { name: '送出回話', exact: true })
    await sendButton.waitFor()
    check('歷史 stale scope 顯示不可送，未恢復當次同意', await sendButton.isDisabled() && await panel.getByLabel('本題專案', { exact: true }).inputValue() === '')
    check('歷史 scope 保留原 snapshot', (await page.getByRole('region', { name: '本次專案範圍' }).innerText()).includes('甲案 · r1'))
    await panel.getByLabel('本題專案', { exact: true }).selectOption('3')
    await sourceChecks.check()
    await panel.getByLabel('確認本題確切目標', { exact: true }).check()
    holdNext = true
    await page.getByRole('button', { name: '產生 Draft Reply', exact: true }).click()
    await page.getByRole('button', { name: '停止串流', exact: true }).waitFor()
    await panel.getByLabel('本題專案', { exact: true }).selectOption('2')
    check('同 PM 多案必須明選，新案核准與同意清空', !await panel.getByLabel('確認本題確切目標', { exact: true }).isChecked())
    await delayed()
    await page.waitForTimeout(100)
    check('同題切乙案丟棄甲案晚到結果', await page.locator('textarea[placeholder^="建議回話"]').count() === 0 && await panel.getByLabel('本題專案', { exact: true }).inputValue() === '2')
    await page.evaluate(() => { location.hash = '#/mentions/8' })
    await page.locator('#main').getByText('合成問題 8', { exact: true }).waitFor()
    check('切 Mention 清除本題專案', await panel.getByLabel('本題專案', { exact: true }).inputValue() === '')
    check('零未知與外站請求', receipt.unknown.length === 0, receipt.unknown)
    check('零前端執行錯誤', receipt.errors.length === 0, receipt.errors)
    await page.screenshot({ path: path.join(output, '04-new-mention.png'), fullPage: true, animations: 'disabled' })
    await context.close()
  } catch (error) {
    receipt.errors.push(error.message)
    if (browser) await browser.contexts()[0]?.pages()[0]?.screenshot({ path: path.join(output, 'failure.png'), fullPage: true, animations: 'disabled' }).catch(() => {})
  } finally {
    if (browser) await browser.close()
    if (server) await new Promise((resolve) => server.close(resolve))
    fs.writeFileSync(path.join(output, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n')
  }
  const passed = receipt.errors.length === 0 && receipt.unknown.length === 0 && receipt.checks.length >= 12 && receipt.checks.every((item) => item.passed)
  console.log(JSON.stringify({ passed, output, checks: receipt.checks, unknown: receipt.unknown, errors: receipt.errors }, null, 2))
  process.exitCode = passed ? 0 : 1
}
main().catch((error) => { console.error(error); process.exitCode = 1 })
