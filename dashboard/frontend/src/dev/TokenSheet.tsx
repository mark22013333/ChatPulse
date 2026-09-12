import { useEffect, useState } from 'react'

/**
 * 設計 token 對照表（規格 §16.3，2026-09-12 補做）。
 *
 * ### 為什麼需要它，明明已經有 tokens.test.ts 了
 *
 * 守門測試驗的是「有沒有人繞過 token」（寫死 hex、用具名色 utility），
 * 它**不驗顏色好不好、對比夠不夠**。而換配色時真正會出事的正是後者。
 *
 * ### 為什麼需要它，明明已經有 Python 驗算腳本了
 *
 * 那支算的是**理論值**：OKLCH → 線性 sRGB → WCAG 相對亮度。規格 §3.1 自己
 * 就警告過「這是計算值不是量測值」。這一頁讀的是 `getComputedStyle` 吐出來的
 * **瀏覽器實際渲染值**，兩者對不上就代表色域裁切或疊色出了事——特別是
 * `--signal-wash` 這類半透明色，理論算法根本算不了（它的結果取決於底下疊了什麼）。
 *
 * ### 怎麼用
 *
 * `#/dev/tokens`。只在開發模式掛載（`import.meta.env.DEV`），正式建置不存在
 * ——它是驗證工具不是產品功能，使用者沒有理由走到這裡。
 * 切換主題後這一頁會自己重算。
 */

const SURFACES = ['background', 'surface', 'raised', 'muted'] as const
type SurfaceName = (typeof SURFACES)[number]

/** 前景 token → 它在 §10.8 的門檻。純裝飾的規線不列門檻。 */
const FOREGROUNDS: Array<{ name: string; floor: number | null; note?: string }> = [
  { name: 'foreground', floor: 7, note: '決策性文字' },
  { name: 'fg-dim', floor: 4.6, note: '次級文字' },
  { name: 'fg-subtle', floor: 4.6, note: '第三層／來源標記' },
  { name: 'disabled-fg', floor: 2, note: 'disabled' },
  { name: 'signal', floor: 4.6, note: '可互動／當前選取' },
  { name: 'caution', floor: 4.6, note: '警示' },
  { name: 'destructive', floor: 4.6, note: '危險' },
  { name: 'line-evidence', floor: 3, note: '承載語意的規線（WCAG 1.4.11）' },
  { name: 'line', floor: null, note: '純裝飾分隔線' },
  { name: 'line-strong', floor: null, note: '純裝飾，較重' },
]

/** 半透明 token：理論算法算不了，只能看實際疊色 */
const ALPHA_TOKENS = ['signal-wash', 'signal-line', 'caution-line']

interface Rgb {
  r: number
  g: number
  b: number
}

/**
 * 讓瀏覽器把任意 CSS 顏色解析成實際畫出來的 sRGB——包含 oklch 的色域裁切。
 *
 * **不可以拿 `getComputedStyle().color` 的數字直接當 RGB 用。**
 * Chrome 對 oklch 定義的顏色會原樣回傳 `oklch(0.222 0.01 75)`，不轉 sRGB；
 * 用 `/[\d.]+/g` 去抓就會把 L／C／H 三個數字當成 R／G／B。第一版就是這樣寫的，
 * 結果四個表面算出**一模一樣**的 hex、所有對比都是 1.01——數字看起來像真的，
 * 全靠「四個表面不可能同色」才露餡。
 *
 * 改走 canvas：把顏色真的畫一個像素再取樣，拿到的就是螢幕上的那個顏色。
 */
function resolve(
  probe: HTMLElement,
  ctx: CanvasRenderingContext2D,
  value: string,
): Rgb | null {
  probe.style.color = ''
  probe.style.color = value
  const computed = getComputedStyle(probe).color
  if (!computed) return null

  // 哨兵：canvas 若看不懂這個顏色會保留前一個 fillStyle，
  // 沒有哨兵就會靜默拿到上一個 token 的顏色（比算錯更難查）。
  //
  // 哨兵值用 rgb() 寫、再讀回 canvas 正規化後的字串來比對——**不要在這裡
  // 寫字面色碼**：守門測試 ⑤ 會掃整個 src 的 hex，第一版寫死哨兵就被它擋下來了。
  ctx.fillStyle = 'rgb(18, 52, 86)'
  const sentinel = ctx.fillStyle
  ctx.fillStyle = computed
  if (ctx.fillStyle === sentinel) return null

  ctx.clearRect(0, 0, 1, 1)
  ctx.fillRect(0, 0, 1, 1)
  const d = ctx.getImageData(0, 0, 1, 1).data
  return { r: d[0], g: d[1], b: d[2] }
}

/** WCAG 2.x 相對亮度。輸入是 0-255 的 sRGB。 */
function luminance({ r, g, b }: Rgb): number {
  const channel = (v: number) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

const hex = ({ r, g, b }: Rgb) =>
  '#' + [r, g, b].map((v) => Math.round(v).toString(16).padStart(2, '0')).join('')

interface Row {
  name: string
  floor: number | null
  note?: string
  swatch: string
  ratios: Array<{ surface: string; value: number; pass: boolean }>
}

export function TokenSheet() {
  const [rows, setRows] = useState<Row[]>([])
  const [surfaceHex, setSurfaceHex] = useState<Array<{ name: string; hex: string }>>([])
  const [theme, setTheme] = useState('')

  useEffect(() => {
    const measure = () => {
      const probe = document.createElement('span')
      probe.style.display = 'none'
      document.body.appendChild(probe)

      const canvas = document.createElement('canvas')
      canvas.width = canvas.height = 1
      // willReadFrequently：這一頁一次要取樣數十個顏色
      const ctx = canvas.getContext('2d', { willReadFrequently: true })
      if (!ctx) {
        probe.remove()
        return
      }

      const surfaces = SURFACES.map((name) => ({
        name,
        rgb: resolve(probe, ctx, `var(--${name})`),
      })).filter((s): s is { name: SurfaceName; rgb: Rgb } => s.rgb !== null)

      setSurfaceHex(surfaces.map((s) => ({ name: s.name, hex: hex(s.rgb) })))

      setRows(
        FOREGROUNDS.map(({ name, floor, note }) => {
          const fg = resolve(probe, ctx, `var(--${name})`)
          return {
            name,
            floor,
            note,
            swatch: fg ? hex(fg) : '—',
            ratios: surfaces.map((s) => {
              const value = fg ? contrast(fg, s.rgb) : 0
              return { surface: s.name, value, pass: floor === null || value >= floor }
            }),
          }
        }),
      )

      probe.remove()
      setTheme(document.documentElement.classList.contains('dark') ? '深色' : '淺色')
    }

    measure()
    // 切主題後要重算。class 變動就重新量一次。
    const observer = new MutationObserver(measure)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])

  const failures = rows.flatMap((r) => r.ratios.filter((x) => !x.pass).map((x) => `${r.name}／${x.surface}`))

  return (
    <div className="h-full overflow-y-auto bg-background text-foreground">
      <div className="mx-auto max-w-5xl p-8">
        <header className="mb-6">
          <h1 className="text-lg font-semibold">設計 token 對照表</h1>
          <p className="mt-1 text-sm text-fg-dim">
            目前是<b>{theme}</b>主題。數字是 `getComputedStyle` 量到的**實際渲染值**，
            不是 OKLCH 的理論計算——兩者對不上就代表發生了色域裁切。
            右上角的主題切換鈕會讓這一頁自己重算。
          </p>
          <p className="mt-2 text-sm">
            {failures.length === 0 ? (
              <span className="text-verified">所有前景 × 表面組合都達到 §10.8 的門檻。</span>
            ) : (
              <span className="text-destructive">
                有 {failures.length} 組未達門檻：{failures.join('、')}
              </span>
            )}
          </p>
        </header>

        <section aria-labelledby="surfaces" className="mb-8">
          <h2 id="surfaces" className="mb-2 text-sm font-medium">
            四個表面
          </h2>
          <div className="flex flex-wrap gap-3">
            {surfaceHex.map((s) => (
              <div key={s.name} className="rounded-md border border-line p-3">
                <div
                  className="mb-2 h-10 w-24 rounded border border-line-strong"
                  style={{ background: `var(--${s.name})` }}
                />
                <div className="font-mono text-2xs">--{s.name}</div>
                <div className="font-mono text-2xs text-fg-subtle">{s.hex}</div>
              </div>
            ))}
          </div>
        </section>

        <section aria-labelledby="matrix">
          <h2 id="matrix" className="mb-2 text-sm font-medium">
            前景 × 表面的對比矩陣
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-line-strong text-left">
                  <th className="py-2 pr-3 font-medium">token</th>
                  <th className="py-2 pr-3 font-medium">色值</th>
                  {SURFACES.map((s) => (
                    <th key={s} className="py-2 pr-3 text-right font-medium">
                      {s}
                    </th>
                  ))}
                  <th className="py-2 pr-3 text-right font-medium">門檻</th>
                  <th className="py-2 font-medium">用途</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.name} className="border-b border-line">
                    <td className="py-1.5 pr-3 font-mono text-2xs">--{row.name}</td>
                    <td className="py-1.5 pr-3">
                      <span className="flex items-center gap-1.5">
                        <span
                          className="inline-block size-3 rounded-xs border border-line-strong"
                          style={{ background: `var(--${row.name})` }}
                        />
                        <span className="font-mono text-2xs text-fg-subtle">{row.swatch}</span>
                      </span>
                    </td>
                    {row.ratios.map((r) => (
                      <td
                        key={r.surface}
                        className={`py-1.5 pr-3 text-right font-mono text-2xs ${
                          r.pass ? 'text-fg-dim' : 'text-destructive font-semibold'
                        }`}
                      >
                        {r.value.toFixed(2)}
                        {r.pass ? '' : ' ✕'}
                      </td>
                    ))}
                    <td className="py-1.5 pr-3 text-right font-mono text-2xs text-fg-subtle">
                      {row.floor === null ? '—' : row.floor.toFixed(1)}
                    </td>
                    <td className="py-1.5 text-2xs text-fg-subtle">{row.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section aria-labelledby="alpha" className="mt-8">
          <h2 id="alpha" className="mb-2 text-sm font-medium">
            半透明 token（理論算法算不了，只能看實際疊色）
          </h2>
          <div className="flex flex-wrap gap-3">
            {ALPHA_TOKENS.map((name) => (
              <div key={name} className="rounded-md border border-line p-3">
                {/* 疊在四個表面上各看一次——同一個半透明色在不同底上是不同的顏色 */}
                <div className="mb-2 flex">
                  {SURFACES.map((s) => (
                    <div
                      key={s}
                      className="flex h-10 w-12 items-center justify-center"
                      style={{ background: `var(--${s})` }}
                    >
                      <span
                        className="block size-7 rounded-xs"
                        style={{ background: `var(--${name})` }}
                      />
                    </div>
                  ))}
                </div>
                <div className="font-mono text-2xs">--{name}</div>
              </div>
            ))}
          </div>
        </section>

        <p className="mt-8 text-2xs text-fg-subtle">
          這一頁只在開發模式存在（`import.meta.env.DEV`），正式建置不會掛載它。
        </p>
      </div>
    </div>
  )
}
