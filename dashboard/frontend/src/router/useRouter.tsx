import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { parseHash, type Route } from '@/lib/route'

interface NavigateOptions {
  /** 取代目前這一筆歷史，不留下可以「上一頁」回來的位置 */
  replace?: boolean
}

interface RouterValue {
  route: Route
  navigate: (hash: string, options?: NavigateOptions) => void
  /** 進入設定覆蓋層之前所在的位置。直接貼設定連結進來時是 '#/summary' */
  previousHash: string
}

const RouterContext = createContext<RouterValue | null>(null)

function currentHash(): string {
  return typeof window === 'undefined' ? '' : window.location.hash
}

/**
 * 薄層 hash router（設計規格 §6.2）。
 *
 * 刻意不用 React Router／TanStack Router：hash 模式下它們多帶的 loader 與
 * action 全是負擔，而且 data router 會把資料綁回畫面生命週期——那正是這個
 * 專案踩過坑才修掉的方向（見 `SummaryWorkspace.tsx` 的 reset 註解）。
 * 這一層只做 URL ↔ 畫面位置的同步，完全不碰資料載入。
 */
export function RouterProvider({ children }: { children: ReactNode }) {
  const [hash, setHash] = useState(currentHash)

  useEffect(() => {
    const onHashChange = () => setHash(window.location.hash)
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  // 空 hash 正規化成 #/summary，讓「重新整理停在原地」對第一次進來的人也成立
  useEffect(() => {
    if (!window.location.hash || window.location.hash === '#') {
      window.history.replaceState(null, '', '#/summary')
      setHash('#/summary')
    }
  }, [])

  const route = useMemo(() => parseHash(hash), [hash])

  /**
   * 設定覆蓋層要知道「進來之前在哪」才關得回去。
   *
   * 不能靠 `history.back()` 推：設定分頁之間切換用的是 replace（不然點五個
   * 分頁就要按五次關閉），history 不再成長，任何以 `history.length` 為準的
   * 啟發式都會失準。改由 router 自己記一份。
   */
  const lastNonSettings = useRef('#/summary')
  useEffect(() => {
    if (route.section !== 'settings') lastNonSettings.current = hash || '#/summary'
  }, [route.section, hash])

  const navigate = useCallback((next: string, options: NavigateOptions = {}) => {
    if (window.location.hash === next) return
    if (options.replace) {
      // replaceState **不會**觸發 hashchange，所以要自己更新 state
      window.history.replaceState(null, '', next)
      setHash(next)
    } else {
      // 設定 location.hash 會觸發 hashchange，讓上面那個 listener 收斂狀態
      window.location.hash = next
    }
  }, [])

  // 相依只有 route／navigate：lastNonSettings 唯一的寫入者是上面那個 effect，
  // 而它與 route 由同一次 hash 變動驅動、且在下一次 render 之前就寫完了，
  // 所以 route 一變就會帶出最新的值。把 ref.current 列進相依陣列反而誤導
  // ——React 不會因為 ref 的內容變了而重算。
  const value = useMemo(
    () => ({ route, navigate, previousHash: lastNonSettings.current }),
    [route, navigate],
  )

  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>
}

export function useRouter(): RouterValue {
  const value = useContext(RouterContext)
  if (!value) throw new Error('useRouter 必須在 RouterProvider 內使用')
  return value
}

/** 只要 route 的捷徑，省得每次都解構。 */
export function useRoute(): Route {
  return useRouter().route
}
