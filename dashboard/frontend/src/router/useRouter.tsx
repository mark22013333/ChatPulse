import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
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

  const value = useMemo(() => ({ route, navigate }), [route, navigate])

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
