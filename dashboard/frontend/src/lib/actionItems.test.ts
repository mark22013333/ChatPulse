import { describe, expect, it } from 'vitest'
import { actionItemsToMarkdown, extractActionItems } from './actionItems'
import { splitDraft } from '@/store/draft'

describe('extractActionItems', () => {
  it('抓出 `• [負責人] 任務` 格式的行', () => {
    const md = [
      '### 討論重點',
      '本週聚焦在部署流程。',
      '',
      '### 待辦事項',
      '• [鄭浩宇] 補上 list_spaces 的分頁測試',
      '• [BU2] 確認 UAT 環境的 Gemini 金鑰',
      '一般段落，不是待辦',
    ].join('\n')

    const items = extractActionItems(md)
    expect(items).toHaveLength(2)
    expect(items[0]).toMatchObject({ owner: '鄭浩宇', task: '補上 list_spaces 的分頁測試' })
    expect(items[1]).toMatchObject({ owner: 'BU2', task: '確認 UAT 環境的 Gemini 金鑰' })
  })

  it('沒有中括號負責人時，退回既有做法掃「待辦事項」段落', () => {
    const md = ['### 待辦事項', '- 補文件', '- 修 CI', '', '### 其他', '- 不該被抓進來'].join('\n')
    const items = extractActionItems(md)
    expect(items.map((item) => item.task)).toEqual(['補文件', '修 CI'])
  })

  it('沒有待辦時回傳空陣列', () => {
    expect(extractActionItems('### 摘要\n只是一段文字。')).toEqual([])
  })

  it('轉成 Markdown 時保留勾選狀態', () => {
    const items = extractActionItems('• [PM] 回覆客戶\n• [RD] 修 bug')
    const md = actionItemsToMarkdown(items, new Set([items[0].key]))
    expect(md).toBe('- [x] **PM**：回覆客戶\n- [ ] **RD**：修 bug')
  })
})

describe('splitDraft', () => {
  it('串流到一半、還沒出現建議回話標題時，全部算脈絡分析', () => {
    const result = splitDraft('### 🧭 脈絡分析\nPM 在問部署時程。')
    expect(result.replyStarted).toBe(false)
    expect(result.context).toBe('PM 在問部署時程。')
    expect(result.reply).toBe('')
  })

  it('兩段都出現時正確切開', () => {
    const raw = '### 🧭 脈絡分析\n背景說明。\n\n### ✍️ 建議回話\n你好，預計下週三上線。'
    const result = splitDraft(raw)
    expect(result.replyStarted).toBe(true)
    expect(result.context.trim()).toBe('背景說明。')
    expect(result.reply.trim()).toBe('你好，預計下週三上線。')
  })
})
