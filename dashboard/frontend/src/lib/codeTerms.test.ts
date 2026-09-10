import { describe, expect, it } from 'vitest'
import { parseCodeTerms } from './codeTerms'

describe('parseCodeTerms', () => {
  it('逗號分隔', () => {
    expect(parseCodeTerms('sendPush,retryCount')).toEqual(['sendPush', 'retryCount'])
  })

  it('空白分隔', () => {
    expect(parseCodeTerms('sendPush retryCount')).toEqual(['sendPush', 'retryCount'])
  })

  it('混用、多餘空白、前後空白都吃', () => {
    expect(parseCodeTerms('  sendPush ,  retryCount,,  UserClue  ')).toEqual([
      'sendPush',
      'retryCount',
      'UserClue',
    ])
  })

  it('**全角逗號與頓號也要當分隔符**', () => {
    // 中文輸入法下最容易打出來的就是它們。不切的話整串會變成一個詞，
    // 而那種失敗從畫面上完全看不出來——只會得到「什麼都沒命中」。
    expect(parseCodeTerms('sendPush，retryCount、UserClue')).toEqual([
      'sendPush',
      'retryCount',
      'UserClue',
    ])
  })

  it('去重但保留順序', () => {
    expect(parseCodeTerms('b, a, b, c, a')).toEqual(['b', 'a', 'c'])
  })

  it('空字串與純空白回空陣列（空陣列＝不覆寫，讓後端自動抽詞）', () => {
    expect(parseCodeTerms('')).toEqual([])
    expect(parseCodeTerms('   ')).toEqual([])
    expect(parseCodeTerms(', ,，')).toEqual([])
  })

  it('保留識別字裡的底線、點號、斜線（那些不是分隔符）', () => {
    expect(parseCodeTerms('user_clue TcPushFail.java core/repository')).toEqual([
      'user_clue',
      'TcPushFail.java',
      'core/repository',
    ])
  })
})
