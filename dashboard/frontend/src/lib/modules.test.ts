import { describe, expect, it } from 'vitest'
import { NAV_MODULES, READY_MODULES, toWorkbench } from '@/lib/modules'

/**
 * 模組表是「這個 app 有哪些頂層工作台」的唯一事實來源，導航、命令面板、
 * 快捷鍵與麵包屑都從它推導。這裡守的是那些推導的前提。
 */

describe('toWorkbench', () => {
  it('三個工作台各自對應', () => {
    expect(toWorkbench('summary')).toBe('summary')
    expect(toWorkbench('mentions')).toBe('mentions')
    expect(toWorkbench('worklog')).toBe('worklog')
  })

  it('設定是覆蓋層，底下維持既有的工作台', () => {
    expect(toWorkbench('settings')).toBe('summary')
  })

  it('每個 ready 模組都對應得出一個工作台', () => {
    // 這條守的是「新增模組卻忘了加進 switch」。TypeScript 的 never 檢查
    // 已經擋住編譯期，這裡再擋一次執行期——因為 switch 的 default 分支
    // 有一個回傳 'summary' 的退路，那個退路會讓漏掉的模組**安靜地**顯示成
    // 摘要工作台，而不是壞給你看。
    for (const module of READY_MODULES) {
      expect(['summary', 'mentions', 'worklog']).toContain(toWorkbench(module.id))
    }
  })
})

describe('NAV_MODULES', () => {
  it('同一組的模組要連續排在一起', () => {
    // NAV_GROUPS 是用 `new Set(map(group))` 從排列推導的，分組若不連續，
    // 導航會出現同一個標題跑出來兩次
    const finished = new Set<string>()
    let current = ''
    for (const module of NAV_MODULES) {
      if (module.group === current) continue
      expect(finished.has(module.group)).toBe(false)
      if (current) finished.add(current)
      current = module.group
    }
  })

  it('快捷鍵不重複', () => {
    // `g` 前綴的第二個鍵。撞到的話 SEQUENCE_MAP 會後蓋前，
    // 其中一個模組的快捷鍵就這樣安靜地失效
    const keys = READY_MODULES.map((m) => m.hotkey).filter(Boolean)
    expect(new Set(keys).size).toBe(keys.length)
  })

  it('ready 的模組一定有 hash，planned 的一定沒有', () => {
    for (const module of NAV_MODULES) {
      if (module.status === 'ready') expect(module.hash).not.toBe('')
      else expect(module.hash).toBe('')
    }
  })

  it('planned 的模組要寫清楚它以後會做什麼', () => {
    // 導航上那句話是使用者唯一的線索，而 tooltip 是被紅線禁止的。
    // 「即將推出」等於沒說——尤其當兩個 planned 模組長得像同一件事時
    for (const module of NAV_MODULES) {
      if (module.status === 'planned') expect(module.plannedNote).toBeTruthy()
    }
  })
})
