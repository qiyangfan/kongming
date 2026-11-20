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
    // 默认 node(含读文件/路径断言的测试);组件测试文件首行以
    // @vitest-environment jsdom 单独切换
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    setupFiles: ["src/test/setup.ts"],
    // CI 下遇到 .only 直接失败:否则整个套件被静默跳过只剩一条用例
    allowOnly: !process.env.CI,
    // 每个用例后恢复 spy/mock,避免跨用例串味造成顺序相关失败
    restoreMocks: true,
    coverage: {
      provider: "v8",
      include: [
        "src/lib/**/*.ts",
        "src/components/ui/**/*.tsx",
        "src/components/data-view.tsx",
        "src/components/empty-state.tsx",
        "src/components/error-alert.tsx",
        "src/components/field-label.tsx",
        "src/components/form-row.tsx",
        "src/components/page-header.tsx",
        "src/components/pagination.tsx",
        "src/components/stat-card.tsx",
        "src/components/status-badge.tsx",
      ],
      exclude: ["src/lib/api-types.gen.ts"],
      reporter: ["text-summary"],
      // 基线防回退门槛,只升不降;组件层门槛按实测值留 3 个百分点余量
      // (lib 38.41/39.85/41.17/26.47,ui 58.33/58.33/62.12/52.58)
      thresholds: {
        "src/lib/**": { lines: 38, statements: 39, branches: 41, functions: 26 },
        "src/components/ui/**": { lines: 55, statements: 55, branches: 59, functions: 49 },
        "src/components/{data-view,empty-state,error-alert,field-label,form-row,page-header,pagination,stat-card,status-badge}.tsx":
          { lines: 95, statements: 95, branches: 85, functions: 90 },
      },
    },
  },
});
