import { expect, test } from "@playwright/test";

test("未登录访问受保护路由被导到登录页", async ({ page }) => {
  // 壳层鉴权外呼统一回 401,验证前端守卫而非后端
  await page.route("**/api/**", (route) => route.fulfill({ status: 401, json: {} }));
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "孔明 Kongming" })).toBeVisible();
});
