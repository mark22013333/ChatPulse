import { describe, expect, it } from 'vitest'
import { MERGE_MAX, isFlatSpace, mergeBlockReason } from '@/lib/merge'
import type { Mention, Space } from '@/lib/types'

/**
 * 這份規則與後端 `resolve_merge_targets()` 是同一條，必須一起改。
 * 兩邊分歧的表現不是報錯，是「勾得起來、送出時才被擋」或更糟的
 * 「前端擋住了一個後端其實允許的組合」——兩種都只會讓人覺得功能壞了。
 */

const DM: Space = {
  id: 'spaces/DM',
  displayName: '李小明',
  type: 'DIRECT_MESSAGE',
  // 實測私訊回報的就是 THREADED_MESSAGES，不是官方文件寫的 UNTHREADED_MESSAGES
  threadingState: 'THREADED_MESSAGES',
  lastActiveTime: null,
}
const GROUP: Space = {
  id: 'spaces/G',
  displayName: '專案群',
  type: 'SPACE',
  threadingState: 'THREADED_MESSAGES',
  lastActiveTime: null,
}
const FLAT_GROUP: Space = { ...GROUP, id: 'spaces/F', threadingState: 'UNTHREADED_MESSAGES' }
const SPACES = [DM, GROUP, FLAT_GROUP]

function mention(id: number, spaceId: string, thread: string): Mention {
  return {
    id,
    space_id: spaceId,
    space_name: 'x',
    message_name: `${spaceId}/messages/${id}`,
    thread_name: `${spaceId}/threads/${thread}`,
    sender_display: '對方',
    create_time: '2026-09-06T10:00:00Z',
    state: 'pending',
    resolved_at: null,
  }
}

describe('isFlatSpace', () => {
  it('私訊即使回報 THREADED_MESSAGES 也算扁平', () => {
    expect(isFlatSpace(DM)).toBe(true)
  })

  it('UNTHREADED_MESSAGES 的群組算扁平', () => {
    expect(isFlatSpace(FLAT_GROUP)).toBe(true)
  })

  it('一般分串群組不算扁平', () => {
    expect(isFlatSpace(GROUP)).toBe(false)
  })

  it('查不到 Space 時保守地當成分串', () => {
    expect(isFlatSpace(undefined)).toBe(false)
  })
})

describe('mergeBlockReason', () => {
  it('還沒勾任何一則時，什麼都能勾', () => {
    expect(mergeBlockReason(mention(1, 'spaces/G', 'a'), [], SPACES)).toBeNull()
  })

  it('已經勾起來的可以取消（不會被自己的規則擋住）', () => {
    const a = mention(1, 'spaces/G', 'a')
    expect(mergeBlockReason(a, [a], SPACES)).toBeNull()
  })

  it('不同聊天室擋下來', () => {
    const a = mention(1, 'spaces/DM', 'a')
    const b = mention(2, 'spaces/G', 'a')
    expect(mergeBlockReason(b, [a], SPACES)).toBe('other-space')
  })

  it('私訊裡不同 thread 仍可合併（每則訊息各自成一串是私訊的常態）', () => {
    const a = mention(1, 'spaces/DM', 'a')
    const b = mention(2, 'spaces/DM', 'b')
    expect(mergeBlockReason(b, [a], SPACES)).toBeNull()
  })

  it('分串群組的不同 thread 擋下來', () => {
    const a = mention(1, 'spaces/G', 'a')
    const b = mention(2, 'spaces/G', 'b')
    expect(mergeBlockReason(b, [a], SPACES)).toBe('other-thread')
  })

  it('分串群組的同一個 thread 可以合併', () => {
    const a = mention(1, 'spaces/G', 'a')
    const b = mention(2, 'spaces/G', 'a')
    expect(mergeBlockReason(b, [a], SPACES)).toBeNull()
  })

  it('不分串群組的不同 thread 可以合併', () => {
    const a = mention(1, 'spaces/F', 'a')
    const b = mention(2, 'spaces/F', 'b')
    expect(mergeBlockReason(b, [a], SPACES)).toBeNull()
  })

  it('Space 清單還沒載入時，退回最保守的判斷（要求同一個 thread）', () => {
    const a = mention(1, 'spaces/DM', 'a')
    const b = mention(2, 'spaces/DM', 'b')
    expect(mergeBlockReason(b, [a], [])).toBe('other-thread')
  })

  it('超過上限擋下來', () => {
    const selected = Array.from({ length: MERGE_MAX }, (_, i) =>
      mention(i + 1, 'spaces/DM', `t${i}`),
    )
    const extra = mention(99, 'spaces/DM', 't99')
    expect(mergeBlockReason(extra, selected, SPACES)).toBe('too-many')
  })

  it('剛好在上限之內還能勾', () => {
    const selected = Array.from({ length: MERGE_MAX - 1 }, (_, i) =>
      mention(i + 1, 'spaces/DM', `t${i}`),
    )
    const extra = mention(99, 'spaces/DM', 't99')
    expect(mergeBlockReason(extra, selected, SPACES)).toBeNull()
  })
})
