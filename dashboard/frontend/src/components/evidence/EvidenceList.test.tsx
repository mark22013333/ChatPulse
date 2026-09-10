import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EvidenceList } from './EvidenceList'
import { toEvidence } from '@/lib/evidence'
import type { DraftPolishMeta, SseMeta } from '@/lib/types'

/**
 * 設計規格 §15.4 的第 2、3 條。
 *
 * 第 2 條（Sepia 三態）：`polished === false` 時 `fallback_reason` 必須用
 * `getByText` 找得到——**不是** `toHaveAttribute('title')`。改版前這類資訊
 * 就活在 title 裡，鍵盤與觸控使用者完全拿不到。
 *
 * 第 3 條（EvidenceList）：餵一份 SseMeta fixture，斷言每一項證據的文字都
 * 找得到。這就是「沒有東西只活在 title 裡」的行為式證明——`lib/tokens.test.ts`
 * 只擋得住「不准新增 title=」，擋不住「這個值除了 title 哪裡都沒有」。
 *
 * 純函式那一層（SseMeta → EvidenceItem[]）已經有 `lib/evidence.test.ts` 的
 * 24 項覆蓋，所以這裡只驗**渲染**：同一份資料畫出來之後讀不讀得到。
 */

const providerLabel = (name: string) => (name === 'claude_cli' ? 'Claude Code' : name)

function draftMeta(overrides: Partial<SseMeta> = {}): SseMeta {
  return {
    type: 'meta',
    mention_id: 47,
    space: '工程討論',
    space_id: 'spaces/AAAAxLxqJxY',
    context: {
      mode: 'thread',
      message_count: 42,
      coverage: 'full',
      time_range: { start: '2026-09-02T06:10:00Z', end: '2026-09-07T03:02:00Z' },
      blocks: [
        { kind: 'thread', label: '原串', count: 38 },
        { kind: 'near', label: '鄰近', count: 4 },
      ],
    },
    image_count: 2,
    provider: 'claude_cli',
    model: 'claude-opus',
    ...overrides,
  } as SseMeta
}

function renderEvidence(
  meta: SseMeta | null,
  polish: DraftPolishMeta | null = null,
  streaming = false,
) {
  const bundle = toEvidence({ origin: 'draft', meta, polish, streaming, providerLabel })
  render(<EvidenceList items={bundle.items} />)
  return bundle
}

const polishMeta = (over: Partial<DraftPolishMeta>): DraftPolishMeta =>
  ({ polished: true, polish_model: 'claude-haiku', ...over }) as DraftPolishMeta

describe('Sepia 三態（規格 §15.4 第 2 條）', () => {
  it('**採用**：畫成 ✓ Sepia 已核對', () => {
    renderEvidence(draftMeta({ reply: { sepia: true } } as Partial<SseMeta>), polishMeta({}))

    // 勾勾與文字在同一個節點裡，所以用 regex 而不是精確比對
    expect(screen.getByText(/✓ Sepia 已核對/)).toBeInTheDocument()
    expect(screen.queryByText(/Sepia 未採用/)).toBeNull()
  })

  it('**未採用**：fallback_reason 是讀得到的文字，而且不准收合', () => {
    renderEvidence(
      draftMeta({ reply: { sepia: true } } as Partial<SseMeta>),
      polishMeta({ polished: false, fallback_reason: '數字 47 被改成 48' }),
    )

    expect(screen.getByText('Sepia 未採用')).toBeInTheDocument()
    const reason = screen.getByText(/數字 47 被改成 48/)
    expect(reason).toBeInTheDocument()

    // critical 的列不准藏在 <details> 後面——誤判「Sepia 到底有沒有生效」
    // 的代價太高，不該要人先點開一個三角形
    expect(reason.closest('details')).toBeNull()
  })

  it('未採用但後端沒給原因時，也要說出「顯示的是未潤稿的版本」', () => {
    renderEvidence(
      draftMeta({ reply: { sepia: true } } as Partial<SseMeta>),
      polishMeta({ polished: false, fallback_reason: undefined }),
    )

    expect(screen.getByText(/潤稿未採用，顯示的是未潤稿的版本/)).toBeInTheDocument()
  })

  it('**進行中**：潤稿發生在串流結束之後，中間那段要說「Sepia 潤稿中」', () => {
    renderEvidence(draftMeta({ reply: { sepia: true } } as Partial<SseMeta>), null, false)

    expect(screen.getByText('Sepia 潤稿中')).toBeInTheDocument()
  })

  it('正對照：沒開潤稿時整列不出現（不是畫一個空白的潤稿列）', () => {
    renderEvidence(draftMeta(), null)

    expect(screen.queryByText(/Sepia/)).toBeNull()
    expect(screen.queryByText('潤稿')).toBeNull()
  })
})

describe('每一項證據的文字都讀得到（規格 §15.4 第 3 條）', () => {
  it('**餵一份完整 meta，每一項的標籤與值都 getByText 找得到**', () => {
    const bundle = renderEvidence(
      draftMeta({
        answering: [
          { mention_id: 47, sender_display: '陳柏元', create_time: '2026-09-10T02:16:39Z' },
          { mention_id: 48, sender_display: '林小美', create_time: '2026-09-10T03:20:00Z' },
        ],
        reference: { spaces: [{ id: 'spaces/BBB', name: '客服回報', count: 12 }] },
        reply: { tone: '工程師', persona: '工程師口吻', sepia: true },
      } as Partial<SseMeta>),
      polishMeta({}),
    )

    // 這條的價值在「逐項」：漏掉哪一項就是那一項只活在別的地方
    expect(bundle.items.length).toBeGreaterThan(4)
    for (const item of bundle.items) {
      expect(
        screen.queryAllByText(item.label).length,
        `證據項「${item.label}」的標籤在畫面上找不到`,
      ).toBeGreaterThan(0)
      if (item.metric) {
        expect(
          screen.queryAllByText(item.metric.value).length,
          `證據項「${item.label}」的數值 ${item.metric.value} 在畫面上找不到`,
        ).toBeGreaterThan(0)
      }
      if (item.summary) {
        expect(
          screen.queryAllByText(new RegExp(escapeRe(item.summary))).length,
          `證據項「${item.label}」的 summary「${item.summary}」在畫面上找不到`,
        ).toBeGreaterThan(0)
      }
    }
  })

  it('**畫出來的內容裡沒有任何 title 屬性**', () => {
    // tokens.test.ts 擋的是原始碼裡不准新增 title=；這條擋的是渲染結果，
    // 連從 props 傳進來的值也不能變成 tooltip。
    const { container } = render(
      <EvidenceList
        items={
          toEvidence({
            origin: 'draft',
            meta: draftMeta({ reply: { sepia: true } } as Partial<SseMeta>),
            polish: polishMeta({ polished: false, fallback_reason: '數字被改動' }),
            streaming: false,
            providerLabel,
          }).items
        }
      />,
    )

    expect(container.querySelectorAll('[title]')).toHaveLength(0)
  })

  it('明細裡的值也在 DOM 裡（收合不等於不存在）', () => {
    renderEvidence(draftMeta())

    // 「原串 38 則／鄰近 4 則」是脈絡那列的明細，收在 <details> 裡。它必須
    // 真的在 DOM，而不是點開才生成——螢幕閱讀器與 Cmd+F 都要找得到。
    const detail = screen.getByText('38 則')
    expect(detail).toBeInTheDocument()
    expect(detail.closest('details')).not.toBeNull() // 確實是收起來的那一份
    expect(screen.getByText('原串')).toBeInTheDocument()
  })
})

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}
