import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ThemeProvider } from 'next-themes'
import App from '@/App'
import { Toaster } from '@/components/ui/sonner'
import { RouterProvider } from '@/router/useRouter'
import '@/index.css'

const container = document.getElementById('root')
if (!container) throw new Error('找不到 #root 掛載點')

createRoot(container).render(
  <StrictMode>
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false} disableTransitionOnChange>
      <RouterProvider>
        <App />
      </RouterProvider>
      <Toaster position="bottom-right" richColors closeButton containerAriaLabel="通知" />
    </ThemeProvider>
  </StrictMode>,
)
