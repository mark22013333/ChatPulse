import { describe, expect, it } from 'vitest'
import { emptyDraft, hasAnyBranch, validateDraft } from '@/store/codeProjects'
import type { CodeEnvironment } from '@/lib/types'

const draftWith = (over: Partial<ReturnType<typeof emptyDraft>>) => ({
  ...emptyDraft(),
  name: '智慧客服後端',
  repo_path: 'D:\\work\\cs-backend',
  branches: { production: 'main' } as Partial<Record<CodeEnvironment, string>>,
  ...over,
})

describe('validateDraft', () => {
  it('完整填寫時通過', () => {
    expect(validateDraft(draftWith({}))).toBeNull()
  })

  it('名稱空白要擋', () => {
    expect(validateDraft(draftWith({ name: '   ' }))).toBe('請填寫專案名稱')
  })

  it('路徑空白要擋', () => {
    expect(validateDraft(draftWith({ repo_path: '' }))).toBe('請填寫專案路徑')
  })

  // 沒有分支對應的專案，正是「查問題時找錯環境」本身——
  // 這個功能的存在理由就是防這件事，所以在送出前就擋。
  it('一個分支對應都沒有要擋', () => {
    const msg = validateDraft(draftWith({ branches: {} }))
    expect(msg).toBe('至少要指定一個環境的分支（正式／UAT／開發）')
  })

  it('只有空白字元的分支名不算數', () => {
    expect(validateDraft(draftWith({ branches: { production: '   ' } }))).toBe(
      '至少要指定一個環境的分支（正式／UAT／開發）',
    )
  })

  // 預設環境沒有分支，等於「按下產生草稿時才會爆」——要在登錄時就講。
  it('預設環境沒有對應分支要擋', () => {
    const msg = validateDraft(
      draftWith({ branches: { uat: 'release/uat' }, default_env: 'production' }),
    )
    expect(msg).toBe('預設環境「正式環境」沒有對應的分支')
  })

  it('預設環境改成有分支的那個就通過', () => {
    expect(
      validateDraft(draftWith({ branches: { uat: 'release/uat' }, default_env: 'uat' })),
    ).toBeNull()
  })
})

describe('hasAnyBranch', () => {
  it('空的分支表回 false', () => {
    expect(hasAnyBranch(emptyDraft())).toBe(false)
  })

  it('任一環境有填就回 true', () => {
    expect(hasAnyBranch(draftWith({ branches: { dev: 'develop' } }))).toBe(true)
  })
})
