import { describe, expect, it } from 'vitest'
import { providerShortName, streamErrorMessage } from './aiErrors'

describe('streamErrorMessage', () => {
  it('兩個配額錯誤都要指出「換一個供應商」這個下一步', () => {
    const claude = streamErrorMessage('CLAUDE_QUOTA_EXCEEDED', 'Claude 用量已達上限')
    const gemini = streamErrorMessage('GEMINI_QUOTA_EXCEEDED', 'Gemini 配額已用盡')
    for (const text of [claude, gemini]) {
      expect(text).toContain('用量已達上限')
      expect(text).toContain('改選其他 AI 供應商')
    }
    // 不可以只是把原始 message 丟出來
    expect(claude).not.toBe('Claude 用量已達上限')
    expect(gemini).not.toBe('Gemini 配額已用盡')
  })

  it('保留後端原始訊息，不吞掉細節', () => {
    expect(streamErrorMessage('CLAUDE_QUOTA_EXCEEDED', '每日上限 100 次')).toContain('每日上限 100 次')
  })

  it('CLAUDE_API_ERROR 給重試與換供應商兩條路', () => {
    const text = streamErrorMessage('CLAUDE_API_ERROR', 'Claude 回應非預期內容')
    expect(text).toContain('重試')
    expect(text).toContain('改選其他 AI 供應商')
  })

  it('供應商名稱打錯（INVALID_PARAMETER）提示重新選一個', () => {
    expect(streamErrorMessage('INVALID_PARAMETER', '不支援的供應商：claud')).toContain('重新選一個')
  })

  it('未知錯誤碼沿用原訊息並附上錯誤碼', () => {
    expect(streamErrorMessage('WEIRD_CODE', '出事了')).toBe('出事了（WEIRD_CODE）')
  })

  it('未知錯誤碼在知道供應商時附註是哪一家', () => {
    expect(streamErrorMessage('WEIRD_CODE', '出事了', 'claude_cli')).toContain('Claude Code CLI')
  })
})

describe('providerShortName', () => {
  it('三個實作名稱都有中文短名', () => {
    expect(providerShortName('claude_api')).toContain('Claude')
    expect(providerShortName('claude_cli')).toContain('Claude Code')
    expect(providerShortName('gemini')).toBe('Gemini')
  })

  it('沒見過的名稱原樣回傳，不要編一個出來', () => {
    expect(providerShortName('grok')).toBe('grok')
  })
})
