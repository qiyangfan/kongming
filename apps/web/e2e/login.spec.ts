import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("登录页只提供统一身份入口且无严重可访问性违规", async ({ page }) => {
  await page.route("**/auth/options", (route) =>
    route.fulfill({ json: { providers: [{ provider: "hufu", display_name: "统一身份" }] } }),
  );
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "孔明 Kongming" })).toBeVisible();
  await expect(page.getByRole("button", { name: "使用 统一身份 登录" })).toBeVisible();
  await expect(page.getByLabel("用户名")).toHaveCount(0);
  await expect(page.getByLabel("密码")).toHaveCount(0);
  const results = await new AxeBuilder({ page }).analyze();
  const blocking = results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical",
  );
  expect(blocking).toEqual([]);
});
