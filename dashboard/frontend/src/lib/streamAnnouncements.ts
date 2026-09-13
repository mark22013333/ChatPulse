/**
 * 串流狀態 → 螢幕閱讀器宣告字串（設計規格 §10.6）。
 *
 * 為什麼要有這一層：**Markdown 容器絕不設 `aria-live`**。每個 chunk 都會重寫
 * innerHTML，設了等於整段內容從頭念一次，而且念到一半又被下一個 chunk 打斷
 * ——比完全不宣告更糟。所以內容層只設 `aria-busy`，另外開一個 app 級的
 * `role="status"` 區域，**只在狀態機轉換時**寫入里程碑。
 *
 * 抽成純函式是為了在 node 環境測得到：這些字串是使用者唯一能得知「現在到
 * 哪了」的管道，寫錯了在畫面上完全看不出來（sr-only）。
 */

export type StreamOrigin = 'summary' | 'draft'

/**
 * 串流的狀態機。
 *
 * `replying` 只有草稿會經過——後端先吐「脈絡分析」再吐「建議回話」，
 * 那個分界是使用者最想知道的一刻（前半段還在鋪陳，後半段才是要送出的東西）。
 */
export type StreamPhase = 'idle' | 'streaming' | 'replying' | 'done' | 'error'

export interface StreamSnapshot {
  streaming: boolean
  error: string | null
  /** 「建議回話」的標題已經串流出來了嗎。摘要一律 false。 */
  replyStarted: boolean
  /** 目前已經產出的字數（完成時用來報「約 N 字」）。 */
  charCount: number
  /** 潤稿結果：true 已採用、false 被退回、null／undefined 沒開潤稿。 */
  polished?: boolean | null
  /** 潤稿被退回的原因。 */
  polishReason?: string | null
}

export interface Announcement {
  text: string
  /**
   * `alert`＝`role="alert"`（打斷正在念的內容）。只有錯誤用它——
   * 其餘一律 polite，不打斷使用者正在讀的東西。
   */
  tone: 'polite' | 'alert'
}

const ORIGIN_LABEL: Record<StreamOrigin, string> = {
  summary: '摘要',
  draft: '草稿',
}

/** 從一份快照算出它處在哪個階段。 */
export function phaseOf(snapshot: StreamSnapshot): StreamPhase {
  if (snapshot.error) return 'error'
  if (snapshot.streaming) return snapshot.replyStarted ? 'replying' : 'streaming'
  return snapshot.charCount > 0 ? 'done' : 'idle'
}

/**
 * 這次狀態轉換該宣告什麼；不值得宣告就回 `null`。
 *
 * **只宣告轉換，不宣告狀態**：同一個階段連續出現不會重複念。這是這一層與
 * 「把 aria-live 掛在內容上」最根本的差別。
 */
export function announce(
  origin: StreamOrigin,
  from: StreamPhase,
  to: StreamPhase,
  snapshot: StreamSnapshot,
): Announcement | null {
  if (from === to) return null
  const what = ORIGIN_LABEL[origin]

  if (to === 'error') {
    return { text: `${what}產生失敗：${snapshot.error ?? '未知原因'}`, tone: 'alert' }
  }

  if (to === 'streaming') {
    // done → streaming 是「重新產生」，說法要不一樣，不然使用者會以為沒反應
    const again = from === 'done' || from === 'error'
    return {
      text: origin === 'draft'
        ? `開始${again ? '重新' : ''}產生回覆草稿，正在讀取脈絡`
        : `開始${again ? '重新' : ''}整理摘要`,
      tone: 'polite',
    }
  }

  if (to === 'replying') {
    // 從 idle 直接跳到 replying（重新整理後補上的舊內容）不值得宣告
    if (from !== 'streaming') return null
    return { text: '脈絡分析完成，開始寫建議回話', tone: 'polite' }
  }

  if (to === 'done') {
    if (from === 'idle') return null // 不是這次跑出來的
    return { text: doneText(origin, snapshot), tone: 'polite' }
  }

  return null
}

/**
 * 完成時的宣告。
 *
 * 三件事一定要講：**完成了**、**多長**、**Sepia 到底有沒有生效**。第三件是
 * 這個產品最貴的誤解之一（以為潤稿過了、其實被退回），而它在畫面上只是一
 * 行小字。
 *
 * **刻意不搶焦點**（規格 §10.6）：完成的瞬間把焦點搬到產出會打斷正在讀舊
 * 內容的人。改成在文字裡說內容在哪、以及一個不必移動焦點就拿得到內容的
 * 快捷鍵，讓使用者自己決定要不要過去。
 *
 * **兩件事都要講，不能只講快捷鍵。** landmark（「主要內容區」）是標準的螢幕
 * 閱讀器導覽、不依賴自訂鍵；⌘⇧C 則是這裡唯一真正有用的快捷鍵——§11.1 表上
 * 沒有「跳到產出」這個鍵，而複製剛好讓使用者不必離開現在的位置就拿到全文。
 *
 * 這句話在 2026-09-10 之前只講 landmark，因為當時 §11.1 只實作了五分之二、
 * 連 ⌘⇧C 都還沒有。**沒有的快捷鍵不可以拿來宣告**（sr-only 的錯誤在畫面上
 * 完全看不出來），所以那時只能講 landmark。`lib/hotkeys.test.ts` 現在有一條
 * 漂移守衛在證明表上的鍵真的存在，這句話才敢寫出鍵名。
 */
function doneText(origin: StreamOrigin, snapshot: StreamSnapshot): string {
  const parts = [`${ORIGIN_LABEL[origin]}完成，約 ${snapshot.charCount} 字`]

  if (origin === 'draft') {
    if (snapshot.polished === true) parts.push('Sepia 已核對')
    else if (snapshot.polished === false) {
      parts.push(`Sepia 未採用${snapshot.polishReason ? `：${snapshot.polishReason}` : ''}`)
    }
  }

  parts.push('內容在主要內容區')
  // 草稿要說清楚複製到的是**建議回話**，不是整份產出（前半段的脈絡分析不是
  // 要送出去的東西）——這正是這個產品最貴的誤解之一
  parts.push(origin === 'draft' ? '按 ⌘⇧C 複製建議回話' : '按 ⌘⇧C 複製全文')
  return parts.join('，') + '。'
}

/**
 * 節流層（規格 §10.6 第 3 條）：串流中每 10 秒一次的進度感。
 *
 * **不隨 chunk**——那樣一秒念好幾次，等於什麼都聽不到。
 */
export function progressAnnouncement(
  origin: StreamOrigin,
  elapsedSeconds: number,
  charCount: number,
): Announcement {
  const what = ORIGIN_LABEL[origin]
  return {
    text: `${what}還在產生，已經過 ${elapsedSeconds} 秒，目前 ${charCount} 字`,
    tone: 'polite',
  }
}

/** 節流層的間隔。 */
export const PROGRESS_INTERVAL_MS = 10_000
