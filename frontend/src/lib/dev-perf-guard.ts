// React 19 dev 构建为 DevTools「Performance」轨道按渲染发 performance.measure（条目以组件名命名，
// 如 Menu / Button / DropdownMenuItem）。这些条目永不自动清，快速切换场会反复重挂 Radix 下拉树 +
// 重渲工作台，measure 缓冲以约千条/次无界增长，持续高频下累积到数百 MB → 渲染进程 OOM 白屏
// （见 memory project_frontend_whitescreen_oom / PR #55）。生产构建里 React 完全不发这些条目
// （实测 measures 恒为 0），故此守卫只在 dev 生效、prod 等价空操作。
//
// 纯函数：超过阈值就清空 user-timing（measure + mark）缓冲，把它钉在一个有界大小。React 只写不读
// 这些条目（仅供工具消费），Radix/React 不依赖回读，清空对应用行为无副作用。
export function clearPerfBufferIfLarge(perf: Performance, threshold: number): boolean {
  if (perf.getEntriesByType("measure").length <= threshold) return false
  perf.clearMeasures()
  perf.clearMarks()
  return true
}
