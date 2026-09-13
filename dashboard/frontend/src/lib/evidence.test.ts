import { describe, expect, it } from 'vitest'
import { toEvidence, type EvidenceItem } from '@/lib/evidence'
import type { DraftPolishMeta, SseMeta } from '@/lib/types'

const providerLabel = (name: string) => (name === 'claude_cli' ? 'Claude Code' : name)

function draftMeta(overrides: Partial<SseMeta> = {}): SseMeta {
  return {
    type: 'meta',
    mention_id: 47,
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
  }
}

function run(meta: SseMeta | null, polish: DraftPolishMeta | null = null, streaming = false) {
  return toEvidence({ origin: 'draft', meta, polish, streaming, providerLabel })
}

function byKind(items: EvidenceItem[], kind: string) {
  return items.filter((i) => i.kind === kind)
}

describe('toEvidence — 缺值與降級', () => {
  it('meta 還沒到、又在串流：只給一個 pending 項', () => {
    const bundle = run(null, null, true)
    expect(bundle.items).toHaveLength(1)
    expect(bundle.items[0].status).toBe('pending')
  })

  it('meta 還沒到、也沒在串流：什麼都不畫', () => {
    expect(run(null, null, false).items).toHaveLength(0)
  })

  it('舊版後端沒回報 context：標 missing，不可偽裝成 ok', () => {
    const meta = draftMeta()
    delete meta.context
    const item = byKind(run(meta).items, 'context')[0]
    expect(item.status).toBe('missing')
    // 「不知道」要說出來，不能只是不顯示
    expect(item.summary).toContain('沒有回報')
  })

  it('舊版後端沒回報 image_count：標 missing，不是 0 張', () => {
    const meta = draftMeta()
    delete meta.image_count
    const item = byKind(run(meta).items, 'images')[0]
    expect(item.status).toBe('missing')
    expect(item.metric).toBeUndefined()
  })

  it('image_count 為 0 是 ok，不是 missing——「讀了但沒有圖」是有意義的答案', () => {
    const item = byKind(run(draftMeta({ image_count: 0 })).items, 'images')[0]
    expect(item.status).toBe('ok')
    expect(item.metric).toEqual({ value: '0', unit: '張' })
  })

  it('脈絡不連續：degraded、附原因、而且不准收合', () => {
    const meta = draftMeta()
    meta.context!.coverage = 'partial'
    const item = byKind(run(meta).items, 'context')[0]
    expect(item.status).toBe('degraded')
    expect(item.critical).toBe(true)
    expect(item.reason).toContain('不保證連續')
  })

  it('圖片被略過：degraded，且略過清單進 detail（不是 title）', () => {
    const meta = draftMeta({ images_skipped: ['a.heic 格式不支援'] })
    const item = byKind(run(meta).items, 'images')[0]
    expect(item.status).toBe('degraded')
    expect(item.detail[0].values).toEqual(['a.heic 格式不支援'])
  })
})

describe('toEvidence — 合併回覆', () => {
  it('單則回覆不畫「回覆對象」那一列（那是廢話）', () => {
    const meta = draftMeta({ answering: [{ mention_id: 47 }] })
    expect(byKind(run(meta).items, 'answering')).toHaveLength(0)
  })

  it('合併多則：列出每一則的寄件人與時間，而且不准收合', () => {
    const meta = draftMeta({
      answering: [
        { mention_id: 47, sender_display: '王小明', create_time: '2026-09-07T01:12:00Z' },
        { mention_id: 48, sender_display: '李美華', create_time: '2026-09-07T02:03:00Z' },
      ],
    })
    const item = byKind(run(meta).items, 'answering')[0]
    expect(item.critical).toBe(true)
    expect(item.metric).toEqual({ value: '2', unit: '則' })
    expect(item.detail.map((d) => d.label)).toEqual(['王小明', '李美華'])
    expect(item.reason).toContain('一起標記為已處理')
  })

  it('沒有寄件人名字時退回「未知成員」，不是空白', () => {
    const meta = draftMeta({
      answering: [{ mention_id: 47 }, { mention_id: 48 }],
    })
    const item = byKind(run(meta).items, 'answering')[0]
    expect(item.detail[0].label).toBe('未知成員')
  })
})

describe('toEvidence — 參考專案', () => {
  const ref = {
    project_name: 'chatpulse-api',
    environment: 'production' as const,
    environment_label: '正式環境',
    branch: 'main',
    commit_sha: 'a1b3c9d',
    commit_date: '2026-08-30T00:00:00Z',
    terms: ['timeout'],
    hit_count: 3,
    files: ['core/draft_context.py', 'core/summary.py'],
    truncated: false,
    notes: [],
  }

  it('有命中：ok，並帶分支與命中檔案', () => {
    const item = byKind(run(draftMeta({ code_refs: [ref] })).items, 'code')[0]
    expect(item.status).toBe('ok')
    expect(item.metric).toEqual({ value: '3', unit: '檔' })
    expect(item.detail.some((d) => d.value === 'main@a1b3c9d')).toBe(true)
    expect(item.detail.find((d) => d.label === '命中')?.values).toEqual(ref.files)
  })

  it('命中 0 筆：degraded，並給一條路去改分支設定', () => {
    const item = byKind(run(draftMeta({ code_refs: [{ ...ref, hit_count: 0, files: [] }] })).items, 'code')[0]
    expect(item.status).toBe('degraded')
    expect(item.reason).toContain('沒有找到相符的程式碼')
    expect(item.action?.href).toBe('#/settings/code-projects')
  })

  it('被略過的專案也要現身，不能安靜地不見', () => {
    const items = byKind(run(draftMeta({ code_skipped: ['legacy-api：分支不存在'] })).items, 'code')
    expect(items).toHaveLength(1)
    expect(items[0].status).toBe('degraded')
    expect(items[0].detail[0].values).toEqual(['legacy-api：分支不存在'])
  })

  it('沒勾參考專案時整組不出現', () => {
    expect(byKind(run(draftMeta()).items, 'code')).toHaveLength(0)
  })
})

describe('toEvidence — 潤稿', () => {
  it('沒開 Sepia 就不畫這一列', () => {
    expect(byKind(run(draftMeta()).items, 'polish')).toHaveLength(0)
  })

  it('開了但還沒有結果：pending', () => {
    const meta = draftMeta({ reply: { sepia: true } })
    const item = byKind(run(meta, null, true).items, 'polish')[0]
    expect(item.status).toBe('pending')
  })

  it('潤稿成功：ok', () => {
    const meta = draftMeta({ reply: { sepia: true } })
    const item = byKind(run(meta, { polisher: 'sepia', polished: true }).items, 'polish')[0]
    expect(item.status).toBe('ok')
    expect(item.summary).toBe('Sepia 已核對')
  })

  it('潤稿未採用：degraded、帶原因、不准收合——這條絕不能顯示成「已採用」', () => {
    const meta = draftMeta({ reply: { sepia: true } })
    const item = byKind(
      run(meta, { polisher: 'sepia', polished: false, fallback_reason: '事實錨點比對未通過' }).items,
      'polish',
    )[0]
    expect(item.status).toBe('degraded')
    expect(item.critical).toBe(true)
    expect(item.reason).toBe('事實錨點比對未通過')
  })

  it('未採用但後端沒給原因時，仍要說「顯示的是未潤稿的版本」', () => {
    const meta = draftMeta({ reply: { sepia: true } })
    const item = byKind(run(meta, { polisher: 'sepia', polished: false }).items, 'polish')[0]
    expect(item.reason).toContain('未潤稿')
  })
})

describe('toEvidence — 順序、計數與摘要', () => {
  it('欄位順序固定：脈絡在最前面，潤稿在最後', () => {
    const meta = draftMeta({
      answering: [{ mention_id: 1 }, { mention_id: 2 }],
      reference_spaces: [{ space_id: 's', space_name: '後端維運', message_count: 120 }],
      reply: { sepia: true, tone_label: '工程師協作' },
    })
    const kinds = run(meta, { polisher: 'sepia', polished: true }).items.map((i) => i.kind)
    expect(kinds[0]).toBe('context')
    expect(kinds[kinds.length - 1]).toBe('polish')
    expect(kinds.indexOf('answering')).toBeLessThan(kinds.indexOf('images'))
  })

  it('attentionCount 數的是非 ok 的項數', () => {
    const meta = draftMeta({ images_skipped: ['x'] })
    meta.context!.coverage = 'partial'
    expect(run(meta).attentionCount).toBe(2)
  })

  it('全部正常時 attentionCount 為 0', () => {
    expect(run(draftMeta()).attentionCount).toBe(0)
  })

  it('供應商顯示名走注入的函式，不 import store', () => {
    const item = run(draftMeta()).items.find((i) => i.kind === 'model')!
    expect(item.summary).toBe('Claude Code')
  })
})

describe('toEvidence — 摘要工作台', () => {
  it('摘要只有來源／附件／生成三列', () => {
    const bundle = toEvidence({
      origin: 'summary',
      meta: {
        type: 'meta',
        space: '後端維運',
        space_id: 'spaces/AAA',
        message_count: 42,
        image_count: 1,
        provider: 'gemini',
      },
      polish: null,
      streaming: false,
      providerLabel,
    })
    expect(bundle.items.map((i) => i.kind)).toEqual(['source', 'images', 'model'])
    expect(bundle.items[0].metric).toEqual({ value: '42', unit: '則' })
  })
})

/**
 * 從紀錄還原的草稿：缺的那幾列要說**對的**理由。
 *
 * `draft_replies.generation_config_json` 只存了 `{provider, model}` ＋ 回覆
 * 設定 ＋ 潤稿結果。脈絡、參考 Space、合併對象、附件張數從來沒存過，所以
 * 還原的草稿必然缺那幾列——**畫成 missing 是對的**（本來就這樣），
 * 錯的是理由：預設那句「這個版本的伺服器沒有回報」會害人去查伺服器版本，
 * 而真正的原因是「這份是還原的，當時沒存」。
 *
 * 狀態不可以因為 restored 而改變：還原的草稿確實沒有那些證據，
 * 把它畫成 ok 才是真正危險的那種錯。
 */
describe('toEvidence — 還原的草稿（restored）', () => {
  const partial = {
    type: 'meta',
    mention_id: 65,
    provider: 'claude_cli',
    model: 'claude-cli:opus',
    reply: { persona_id: 1, persona_name: '羅振宇（羅胖）', sepia: true },
  } as unknown as SseMeta

  const restoredBundle = () =>
    toEvidence({ origin: 'draft', meta: partial, polish: null, streaming: false, providerLabel, restored: true })

  it('**脈絡那一列說「沒有保存」，不說「伺服器沒回報」**', () => {
    const context = byKind(restoredBundle().items, 'context')[0]
    expect(context.status).toBe('missing')
    expect(context.summary).toMatch(/沒有保存/)
    expect(context.summary).not.toMatch(/伺服器/)
  })

  it('附件那一列同理', () => {
    const images = byKind(restoredBundle().items, 'images')[0]
    expect(images.status).toBe('missing')
    expect(images.summary).toMatch(/沒有保存/)
  })

  it('**正對照：沒有 restored 時仍然說「伺服器沒回報」**', () => {
    // 少了這條，「永遠說沒有保存」也會讓上面兩條通過——而那對真正的
    // 舊版後端是錯的訊息。
    const context = byKind(run(partial).items, 'context')[0]
    expect(context.summary).toMatch(/伺服器/)
    expect(context.summary).not.toMatch(/沒有保存/)
  })

  it('restored 只改理由、不改狀態（不可以因此畫成 ok）', () => {
    const context = byKind(restoredBundle().items, 'context')[0]
    const images = byKind(restoredBundle().items, 'images')[0]
    expect(context.status).toBe('missing')
    expect(images.status).toBe('missing')
  })

  it('存下來的那三列照樣讀得出來（生成／回話設定）', () => {
    const items = restoredBundle().items
    expect(byKind(items, 'model')[0].summary).toBeTruthy()
    const reply = byKind(items, 'reply-setting')[0]
    expect(reply.detail.some((d) => d.value === '羅振宇（羅胖）')).toBe(true)
  })
})
