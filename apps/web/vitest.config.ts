import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";

/* 图表引擎通过动态导入按需加载；主题切换重挂渲染器时保留用户选择的图表类型。 */
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
