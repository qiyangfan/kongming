import { expect, test } from "@playwright/test";

test("点击统一身份登录跳转 IdP 授权页", async ({ page }) => {
  let started = false;
  await page.route("**/api/**", async (route) => {
    const url = route.request().url();
    if (url.includes("/admin-auth/keycloak/start")) {
      started = true;
      await route.fulfill({
        status: 200,
        json: { authorize_url: "/fake-idp?x=1" },
      });
      return;
    }
    await route.fulfill({ status: 401, json: {} });
  });
  await page.goto("/login");
  await page.getByRole("button", { name: "使用统一身份登录" }).click();
  await expect.poll(() => started).toBe(true);
  await page.waitForURL(/fake-idp/);
});
