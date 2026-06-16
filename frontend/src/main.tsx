import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { TooltipProvider } from "@/components/ui/tooltip"
import App from "./App.tsx"
import { clearPerfBufferIfLarge } from "@/lib/dev-perf-guard"
import "./index.css"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, staleTime: 30_000 },
  },
})

// dev：钉住 React 19 无界增长的 performance.measure 缓冲，防止快速切换场累积到 OOM 白屏。
// prod 构建里 React 不发这些条目，下面整段等价空操作（measures 恒为 0）。见 lib/dev-perf-guard。
if (import.meta.env.DEV) {
  setInterval(() => clearPerfBufferIfLarge(performance, 8_000), 1_000)
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={200}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>,
)
