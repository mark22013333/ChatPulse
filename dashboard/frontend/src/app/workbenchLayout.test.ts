import { describe, expect, it } from 'vitest'
import { workbenchLayout } from '@/app/workbenchLayout'

describe('workbenchLayout', () => {
  it('表格型（摘要工作台）在最寬的斷點也是主從切換：沒有 id 顯示表格、有 id 顯示工作區', () => {
    expect(workbenchLayout('summary', false, 'wide')).toMatchObject({
      listHidden: false,
      mainHidden: true,
      listFullWidth: true,
      evidenceHidden: true,
      backEverywhere: true,
    })
    expect(workbenchLayout('summary', true, 'wide')).toMatchObject({
      listHidden: true,
      mainHidden: false,
      evidenceHidden: false,
    })
  })

  it('正對照：對話型（Mention 收件匣）在 ≥1024 仍然清單與工作區並排', () => {
    expect(workbenchLayout('mentions', true, 'wide')).toEqual({
      listHidden: false,
      mainHidden: false,
      listFullWidth: false,
      evidenceHidden: false,
      backEverywhere: false,
    })
    expect(workbenchLayout('mentions', true, 'medium').listHidden).toBe(false)
  })

  it('對話型在 768–1024 照舊主從切換', () => {
    expect(workbenchLayout('mentions', true, 'narrow')).toMatchObject({
      listHidden: true,
      mainHidden: false,
    })
    expect(workbenchLayout('mentions', false, 'narrow')).toMatchObject({
      listHidden: false,
      mainHidden: true,
      // 證據欄只有表格型會收：對話型在窄寬度本來就沒有常駐證據欄（xl:flex）
      evidenceHidden: false,
    })
  })

  // S4 改寫：原本這條是「S4 之前仍是對話型」（listHidden=false），S4 把工時草稿
  // 改成表格型之後那條斷言本來就該反過來。改成與摘要工作台同一組完整斷言
  it('工時草稿（S4 起）是表格型：沒有日期顯示表格、有日期顯示單日工作區', () => {
    expect(workbenchLayout('worklog', false, 'wide')).toEqual({
      listHidden: false,
      mainHidden: true,
      listFullWidth: true,
      evidenceHidden: true,
      backEverywhere: true,
    })
    expect(workbenchLayout('worklog', true, 'wide')).toMatchObject({
      listHidden: true,
      mainHidden: false,
      evidenceHidden: false,
    })
  })
})
