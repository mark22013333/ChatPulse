import { useEffect } from 'react'
import { useRoute } from '@/router/useRouter'
import { useMentionsStore } from '@/store/mentions'
import { useSpacesStore } from '@/store/spaces'

/**
 * URL → store 的單向同步（設計規格 §6.6）。
 *
 * URL 是「現在在看哪一個 Space／哪一則 Mention」的唯一真相。使用者點清單時
 * 呼叫 `navigate()` 而不是直接 `select()`，這樣「點擊」與「貼網址」走同一條
 * 路徑，只有一種行為要維護。
 *
 * **去重刻意放在這一層，不去改 store 的 `select`。** 把早退塞進既有的 `select`
 * 會動到既有 export 的行為（紅線 3）；而且 `mentions.select` 有個副作用是清掉
 * `external`——從摘要工作台建立的草稿目標不在收件匣清單裡，靠 `external` 撐著。
 * 那條路徑會先 `selectExternal()`（它自己會設好 `selectedId`）再導航，所以這裡
 * 的去重一比對就跳過，`external` 得以保留。少了這個去重，切過去的瞬間就會被
 * 清成空白工作區。
 */
export function useRouteSync() {
  const route = useRoute()

  useEffect(() => {
    if (route.section !== 'summary') return
    const target = route.spaceId
    if (useSpacesStore.getState().selectedId === target) return
    useSpacesStore.getState().select(target)
  }, [route.section, route.spaceId])

  useEffect(() => {
    if (route.section !== 'mentions') return
    const target = route.mentionId
    if (useMentionsStore.getState().selectedId === target) return
    useMentionsStore.getState().select(target)
  }, [route.section, route.mentionId])

  // `?merge=` → 勾選狀態。route.mergeIds 每次解析都是新陣列，所以一定要比內容。
  useEffect(() => {
    if (route.section !== 'mentions') return
    const fromUrl = route.mergeIds
    const inStore = useMentionsStore.getState().mergeIds
    if (sameIds(fromUrl, inStore)) return
    // URL **不表達**「只勾了一則」這個過渡狀態——hashForMentions 兩則以上才帶
    // merge（route.test.ts 有一條守著）。所以網址上沒有 merge 時，不可以把
    // 單獨一則的勾選清掉，否則使用者勾第一則的瞬間它就會自己彈回去。
    if (fromUrl.length === 0 && inStore.length <= 1) return
    useMentionsStore.getState().setMergeIds(fromUrl)
  }, [route.section, route.mergeIds])
}

function sameIds(a: number[], b: number[]): boolean {
  // 順序有意義：第一個是主要那則，回話會送到它的討論串
  return a.length === b.length && a.every((id, i) => id === b[i])
}
