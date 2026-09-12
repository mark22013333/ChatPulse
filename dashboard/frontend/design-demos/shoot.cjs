/**
 * 把三版設計稿截成明暗各一張，輸出到 `shots/`（不進版控，隨時可重跑）。
 *
 *   node design-demos/shoot.js
 *
 * 需要 playwright（全域或專案內皆可）：
 *   NODE_PATH=$(npm root -g) node design-demos/shoot.js
 *
 * ### 一個踩過的坑：不要用 classList 硬切主題
 *
 * 第一版是直接 `documentElement.classList.add('dark')` 來截深色。顏色確實變了
 * （那是純 CSS），但**任何依賴 JS 狀態的東西都不會更新**——v2 的左側導覽因此
 * 保持淺色、按鈕文字停在「切換為暗色主題」，看起來像 v2 有 bug，實際上是
 * 觀測手法自己製造出來的假缺陷。改成點它自己的切換鈕，並在事後比對
 * 導覽與 body 的亮度差當作交叉檢查。
 */
const { chromium } = require('playwright')
const path = require('path')
const fs = require('fs')

const DEMO_DIR = __dirname
const OUT_DIR = path.join(DEMO_DIR, 'shots')
const VERSIONS = [
  { file: 'v1-restrained.html', slug: 'v1' },
  { file: 'v2-standard.html', slug: 'v2' },
  { file: 'v3-dense.html', slug: 'v3' },
]

const themeOf = (page) =>
  page.evaluate(() => (document.documentElement.className.includes('dark') ? 'dark' : 'light'))

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true })
  const browser = await chromium.launch()
  const results = []

  for (const { file, slug } of VERSIONS) {
    const src = path.join(DEMO_DIR, file)
    if (!fs.existsSync(src)) {
      results.push(`${slug}: 檔案不存在，略過`)
      continue
    }

    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
    const errors = []
    page.on('pageerror', (e) => errors.push(String(e)))
    page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

    await page.goto('file://' + src, { waitUntil: 'load' })
    await page.waitForTimeout(400)

    const first = await themeOf(page)
    await page.screenshot({ path: path.join(OUT_DIR, `${slug}-${first}.png`) })

    const toggle = page
      .locator('button, [role="button"], a')
      .filter({ hasText: /深色|暗色|亮色|淺色|主題|theme/i })
      .first()
    if (await toggle.count()) await toggle.click()
    else await page.evaluate(() => document.documentElement.classList.toggle('dark'))
    await page.waitForTimeout(350)

    const second = await themeOf(page)
    if (second === first) {
      results.push(`${slug}: ⚠ 切換後主題沒變，找不到切換鈕？`)
      await page.close()
      continue
    }
    await page.screenshot({ path: path.join(OUT_DIR, `${slug}-${second}.png`) })

    const probe = await page.evaluate(() => {
      const de = document.documentElement
      const nav = document.querySelector('nav') || document.querySelector('aside')
      const lum = (rgb) => {
        const m = (rgb || '').match(/\d+(\.\d+)?/g)
        if (!m) return null
        const [r, g, b] = m.map(Number)
        return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
      }
      return {
        overflow: de.scrollWidth > de.clientWidth,
        bodyLum: lum(getComputedStyle(document.body).backgroundColor),
        navLum: nav ? lum(getComputedStyle(nav).backgroundColor) : null,
      }
    })

    // 深色態下導覽比 body 亮太多 ⇒ 它沒跟著換主題（多半是切換手法繞過了 JS）
    const navOk =
      probe.bodyLum === null || probe.navLum === null || probe.navLum - probe.bodyLum <= 0.3
    results.push(
      `${slug}: ${first}→${second}｜${probe.overflow ? '⚠ 水平溢出' : '無溢出'}｜` +
        `${navOk ? '導覽亮度正常' : '⚠ 導覽沒跟著換主題'}｜` +
        (errors.length ? `⚠ ${errors.length} 錯誤` : '無錯誤'),
    )
    await page.close()
  }

  await browser.close()
  console.log(results.join('\n'))
  console.log('\n輸出：' + OUT_DIR)
}

main().catch((e) => {
  console.error('失敗:', e)
  process.exit(1)
})
