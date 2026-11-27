/* 主题首帧:app 模块未执行时也应由 index.html 内联脚本应用已存偏好。 */

import { expect, test } from "@playwright/test";

test("登录页首帧应用已存暗色偏好", async ({ page }) => {
  await page.route("**/*.js", (route) => route.abort());
  await page.addInitScript(() => localStorage.setItem("km-theme", "dark"));
  await page.goto("/login", { waitUntil: "domcontentloaded" });
  await expect(page.locator("html")).toHaveClass(/dark/);
});

test("登录页无手动值时跟随系统暗色", async ({ page }) => {
  await page.route("**/*.js", (route) => route.abort());
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/login", { waitUntil: "domcontentloaded" });
  await expect(page.locator("html")).toHaveClass(/dark/);
});
