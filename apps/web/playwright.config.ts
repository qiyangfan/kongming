import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  // CI 下遇到 .only 直接失败,避免其余用例被静默跳过
  forbidOnly: !!process.env.CI,
  use: { baseURL: "http://127.0.0.1:4173" },
  webServer: {
    command: "pnpm preview --host 127.0.0.1 --port 4173 --strictPort",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
  },
});
