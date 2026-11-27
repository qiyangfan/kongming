import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";

const REPO_ROOT = fileURLToPath(new URL("../..", import.meta.url));
const WEB_SRC = fileURLToPath(new URL("./src", import.meta.url));

/* 共享包在仓库根下(packages/*),覆盖率与测试根都提到仓库根,
   否则 v8 不统计项目根之外的源文件。 */
export default defineConfig({
  root: REPO_ROOT,
  resolve: {
    alias: {
      "@": WEB_SRC,
    },
  },
  test: {
    environment: "node",
    include: ["apps/web/src/**/*.test.ts", "apps/web/src/**/*.test.tsx"],
    setupFiles: [fileURLToPath(new URL("./src/test/setup.ts", import.meta.url))],
    allowOnly: !process.env.CI,
    restoreMocks: true,
    coverage: {
      provider: "v8",
      include: [
        "packages/api-client/src/**/*.ts",
        "packages/ui/src/{alert-dialog,avatar,badge,button,card,checkbox,collapsible,dialog,dropdown-menu,input,label,scroll-area,select,separator,sheet,skeleton,switch,table,textarea,toggle-group,toggle,tooltip}.tsx",
        "packages/ui/src/{data-view,empty-state,error-alert,field-label,form-row,page-header,pagination,stat-card,status-badge}.tsx",
      ],
      exclude: ["**/*.gen.*"],
      reporter: ["text-summary"],
      thresholds: {
        "packages/api-client/src/**": { lines: 38, statements: 39, branches: 41, functions: 26 },
        "packages/ui/src/{alert-dialog,avatar,badge,button,card,checkbox,collapsible,dialog,dropdown-menu,input,label,scroll-area,select,separator,sheet,skeleton,switch,table,textarea,toggle-group,toggle,tooltip}.tsx":
          { lines: 55, statements: 55, branches: 59, functions: 49 },
        "packages/ui/src/{data-view,empty-state,error-alert,field-label,form-row,page-header,pagination,stat-card,status-badge}.tsx":
          { lines: 95, statements: 95, branches: 85, functions: 90 },
      },
    },
  },
});
