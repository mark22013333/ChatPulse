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
}
