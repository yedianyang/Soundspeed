import { test, expect } from "@playwright/test"

// 前置:后端已 SOUNDSPEED_DEV=1 起并 seed 多个有剧本的场、前端 dev 已起。
// 回归 React 19 dev 的 performance.measure 无界泄漏:任何高频重渲都往里堆条目(用户场景=剧本面板
// 左右翻场 chevron 快速切换 → 长会话累积到 OOM 白屏,见 lib/dev-perf-guard /
// memory project_frontend_whitescreen_oom)。这里用 Scene 下拉切场来驱动增长:它每次重挂 Radix
// 下拉树、堆条目最快(约千条/次),能在一条测试内把无修复版推到 10 万级;翻场 chevron 是同一缓冲、
// 只是约 18 条/次更慢。dev-perf-guard 把缓冲钉在有界大小,本测断言它守在 6 万以下。
// 注:prod 构建里 React 不发 measure(恒 0),该泄漏 dev only,故此回归只在 dev 有意义。
test("快速切换场不让 performance.measure 缓冲无界增长", async ({ page }) => {
  const pageErrors: string[] = []
  page.on("pageerror", (e) => pageErrors.push(String(e)))

  await page.goto("/admin", { waitUntil: "networkidle" })
  const sceneTrigger = page.getByRole("button").filter({ hasText: "Scene" }).last()
  await expect(sceneTrigger).toBeVisible()

  for (let r = 0; r < 120; r++) {
    await sceneTrigger.click({ timeout: 1500 }).catch(() => {})
    const items = page.locator('[role="menuitem"]')
    const n = await items.count()
    if (n > 1) {
      await items.nth(r % (n - 1)).click({ timeout: 1500 }).catch(() => {})
    } else {
      await page.keyboard.press("Escape").catch(() => {})
    }
    await page.waitForTimeout(8)
  }
  // 给守卫的 1s interval 至少跑一拍再采样。
  await page.waitForTimeout(1300)

  const measures = await page.evaluate(
    () => performance.getEntriesByType("measure").length,
  )
  expect(measures).toBeLessThan(60_000)
  expect(pageErrors).toEqual([])
})
