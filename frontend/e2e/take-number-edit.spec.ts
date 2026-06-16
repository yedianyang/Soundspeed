import { test, expect } from "@playwright/test"

// 前置:后端已 SOUNDSPEED_DEV=1 起、已 seed 至少一条 take;前端 dev 已起。
// 验 harness 能驱动既有 UI:改 Take 编号 → 提交 → 持久化。
test("编辑 Take 编号并持久化", async ({ page }) => {
  await page.goto("/admin")
  const takeBadge = page.getByText(/^Take /).first()
  await expect(takeBadge).toBeVisible()
  await takeBadge.click()
  const input = page.getByPlaceholder("例：5")
  await expect(input).toBeVisible()
  await input.fill("7")
  await input.press("Enter")
  await expect(page.getByText(/^Take 7/).first()).toBeVisible()
  await page.reload()
  await expect(page.getByText(/^Take 7/).first()).toBeVisible()
})
