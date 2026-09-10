import { describe, expect, it } from 'vitest'
import {
  announce,
  phaseOf,
  progressAnnouncement,
  type StreamSnapshot,
} from './streamAnnouncements'
import { resolveHotkey } from './hotkeys'

function snap(over: Partial<StreamSnapshot> = {}): StreamSnapshot {
  return { streaming: false, error: null, replyStarted: false, charCount: 0, ...over }
}

describe('phaseOf', () => {
  it('什麼都沒發生＝idle', () => {
    expect(phaseOf(snap())).toBe('idle')
  })

  it('串流中、還沒到建議回話＝streaming', () => {
    expect(phaseOf(snap({ streaming: true, charCount: 120 }))).toBe('streaming')
  })

  it('串流中、建議回話開始了＝replying', () => {
    expect(phaseOf(snap({ streaming: true, replyStarted: true, charCount: 300 }))).toBe('replying')
  })

  it('停了但有內容＝done', () => {
    expect(phaseOf(snap({ charCount: 300 }))).toBe('done')
  })

  it('**錯誤優先於一切**（串流中出錯也是 error）', () => {
    expect(phaseOf(snap({ streaming: true, error: '配額用完了' }))).toBe('error')
    expect(phaseOf(snap({ charCount: 300, error: '配額用完了' }))).toBe('error')
  })

  it('停了、也沒有內容＝idle（不是 done）', () => {
    // 按了停止串流、什麼都沒吐出來的情況。宣告「完成」是錯的
    expect(phaseOf(snap({ streaming: false, charCount: 0 }))).toBe('idle')
  })
})

describe('announce：只宣告轉換，不宣告狀態', () => {
  it('**同一個階段不重複念**', () => {
    expect(announce('draft', 'streaming', 'streaming', snap({ streaming: true }))).toBeNull()
    expect(announce('summary', 'done', 'done', snap({ charCount: 5 }))).toBeNull()
  })

  it('開始：摘要與草稿說法不同', () => {
    expect(announce('summary', 'idle', 'streaming', snap({ streaming: true }))?.text).toBe(
      '開始整理摘要',
    )
    expect(announce('draft', 'idle', 'streaming', snap({ streaming: true }))?.text).toBe(
      '開始產生回覆草稿，正在讀取脈絡',
    )
  })

  it('**重新產生要說「重新」**，不然使用者會以為沒反應', () => {
    expect(announce('draft', 'done', 'streaming', snap({ streaming: true }))?.text).toContain(
      '重新',
    )
    expect(announce('draft', 'error', 'streaming', snap({ streaming: true }))?.text).toContain(
      '重新',
    )
    // 第一次就不該說「重新」
    expect(announce('draft', 'idle', 'streaming', snap({ streaming: true }))?.text).not.toContain(
      '重新',
    )
  })

  it('脈絡分析完成那一刻要講——那是使用者最想知道的分界', () => {
    const a = announce('draft', 'streaming', 'replying', snap({ streaming: true, replyStarted: true }))
    expect(a?.text).toBe('脈絡分析完成，開始寫建議回話')
    expect(a?.tone).toBe('polite')
  })

  it('idle → replying 不宣告（重新整理後補上的舊內容，不是這次跑的）', () => {
    expect(
      announce('draft', 'idle', 'replying', snap({ streaming: true, replyStarted: true })),
    ).toBeNull()
  })

  it('idle → done 不宣告（同上）', () => {
    expect(announce('summary', 'idle', 'done', snap({ charCount: 500 }))).toBeNull()
  })

  it('**錯誤走 alert（會打斷），其餘一律 polite**', () => {
    const err = announce('draft', 'streaming', 'error', snap({ error: 'AI 供應商沒有回應' }))
    expect(err?.tone).toBe('alert')
    expect(err?.text).toContain('AI 供應商沒有回應')

    // 正對照：完成與開始都不打斷
    expect(announce('draft', 'idle', 'streaming', snap({ streaming: true }))?.tone).toBe('polite')
    expect(announce('draft', 'replying', 'done', snap({ charCount: 10 }))?.tone).toBe('polite')
  })

  it('錯誤訊息拿不到時也要說得出一句話（不是空白）', () => {
    expect(announce('summary', 'streaming', 'error', snap({ error: null }))?.text).toBe(
      '摘要產生失敗：未知原因',
    )
  })
})

describe('完成的宣告：三件事都要講', () => {
  it('字數要講（使用者要判斷這份長度合不合理）', () => {
    expect(announce('summary', 'streaming', 'done', snap({ charCount: 482 }))?.text).toContain(
      '約 482 字',
    )
  })

  it('**Sepia 採用了要講**', () => {
    const a = announce('draft', 'replying', 'done', snap({ charCount: 300, polished: true }))
    expect(a?.text).toContain('Sepia 已核對')
  })

  it('**Sepia 被退回要講，還要講原因**——以為潤稿生效其實沒有，是最貴的誤解之一', () => {
    const a = announce(
      'draft',
      'replying',
      'done',
      snap({ charCount: 300, polished: false, polishReason: '數字 47 被改成 48' }),
    )
    expect(a?.text).toContain('Sepia 未採用：數字 47 被改成 48')
  })

  it('沒開潤稿時不提 Sepia（不是說「Sepia 未採用」）', () => {
    const a = announce('draft', 'replying', 'done', snap({ charCount: 300, polished: null }))
    expect(a?.text).not.toContain('Sepia')
  })

  it('摘要不提 Sepia（潤稿只作用於建議回話）', () => {
    const a = announce('summary', 'streaming', 'done', snap({ charCount: 300, polished: true }))
    expect(a?.text).not.toContain('Sepia')
  })

  it('**告知內容在哪，但不搶焦點**', () => {
    // 完成的瞬間把焦點搬過去會打斷正在讀舊內容的人（規格 §10.6）
    expect(announce('draft', 'replying', 'done', snap({ charCount: 300 }))?.text).toContain(
      '內容在主要內容區',
    )
  })

  it('**告知一個真的存在的快捷鍵**（§10.6 要求，§11.1 補完後才做得到）', () => {
    // 這句話在 §11.1 只實作五分之二時只能講 landmark——沒有的快捷鍵不可以
    // 拿來宣告。sr-only 的錯誤在畫面上完全看不出來，所以這裡除了字串本身，
    // 還要證明那個鍵真的解析得出動作（下一條）
    expect(announce('summary', 'streaming', 'done', snap({ charCount: 300 }))?.text).toContain(
      '按 ⌘⇧C 複製全文',
    )
    // 草稿要說複製到的是**建議回話**，不是整份產出（前半段的脈絡分析不送出）
    expect(announce('draft', 'replying', 'done', snap({ charCount: 300 }))?.text).toContain(
      '按 ⌘⇧C 複製建議回話',
    )
  })

  it('**宣告裡提到的 ⌘⇧C 真的有實作**（不是照著規格抄一句話）', () => {
    // 這條把宣告字串與解析器綁在一起。改動 resolveHotkey 的 copy 分支時，
    // 這裡會紅——而不是讓螢幕閱讀器使用者去按一個不存在的鍵
    expect(resolveHotkey({ key: 'C', metaKey: true, shiftKey: true }, false)).toBe('copy')
  })
})

describe('progressAnnouncement（節流層）', () => {
  it('講經過秒數與目前字數', () => {
    const a = progressAnnouncement('draft', 20, 640)
    expect(a.text).toBe('草稿還在產生，已經過 20 秒，目前 640 字')
    expect(a.tone).toBe('polite')
  })

  it('摘要用自己的說法', () => {
    expect(progressAnnouncement('summary', 10, 88).text).toContain('摘要還在產生')
  })
})
