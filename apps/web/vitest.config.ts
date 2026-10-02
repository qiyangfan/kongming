import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";

/* 最小测试网(066):node 环境、零 jsdom、零 DOM 挂载——收 src/lib/** 纯函数、
   components 的静态守卫(图表懒加载边界)与标准件渲染守卫(react-dom/server
   静态出串,不挂 DOM 不测交互);构建段与 vite.config.ts 分离,
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
