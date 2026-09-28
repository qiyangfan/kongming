import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";

/* 最小测试网(066):仅覆盖 src/lib/** 纯函数(与 components 的图表懒加载边界
   静态守卫),node 环境、零 jsdom、零组件测试;构建段与 vite.config.ts 分离,
   测试不得影响 build/lint。 */
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
