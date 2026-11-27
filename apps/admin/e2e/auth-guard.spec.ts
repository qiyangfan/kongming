import { expect, test } from "@playwright/test";

test("未登录访问管理页被导到管理台登录页", async ({ page }) => {
  await page.route("**/api/**", (route) => route.fulfill({ status: 401, json: {} }));
  await page.goto("/users");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "孔明管理台" })).toBeVisible();
});
