import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import jsxA11y from "eslint-plugin-jsx-a11y";
import sonarjs from "eslint-plugin-sonarjs";
import testingLibrary from "eslint-plugin-testing-library";
import playwright from "eslint-plugin-playwright";
// 放在所有配置之后:只关掉与 prettier 冲突的格式规则,格式判断单一真源归 prettier
import eslintConfigPrettier from "eslint-config-prettier";

export default tseslint.config(
  { ignores: ["dist", "src/lib/api-types.gen.ts", "*.gen.ts"] },
  {
    linterOptions: { reportUnusedDisableDirectives: "error" },
    extends: [
      js.configs.recommended,
      ...tseslint.configs.recommended,
      jsxA11y.flatConfigs.recommended,
      // sonarjs 推荐集:认知复杂度(默认 15)、重复分支、嵌套三元/模板、灾难性回溯正则等
      sonarjs.configs.recommended,
    ],
    files: ["**/*.{ts,tsx}"],
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": "off",
      // 结构体积门禁(ESLint 核心规则无内置默认值,取业界常用口径):
      // 圈复杂度 15、单文件 500 行、单函数 150 行。
      complexity: ["error", 15],
      "max-lines": ["error", 500],
      "max-lines-per-function": ["error", 150],
      "max-depth": ["error", 4],
      "max-params": ["error", 3],
      // Checkbox 是 Radix 自定义控件,label 关联需显式声明控件组件
      "jsx-a11y/label-has-associated-control": [
        "error",
        { controlComponents: ["Checkbox"], depth: 3 },
      ],
    },
  },
  {
    // 语句计数只约束过程式逻辑;组件/hook 以声明式组合为主(语句数≈hook 调用数),
    // 其复杂度由上面 complexity/行数/depth/params 守护。
    files: ["src/lib/**/*.ts"],
    rules: { "max-statements": ["error", 10] },
  },
  {
    // 类型感知规则:no-floating-promises / no-misused-promises 等需要 tsc 项目,
    // 只覆盖 tsconfig 纳入的文件;e2e 与配置文件同受 tsc 检查
    files: ["src/**/*.{ts,tsx}", "e2e/**/*.ts", "*.config.ts"],
    extends: [...tseslint.configs.recommendedTypeChecked],
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    rules: {
      // props 在本仓一律解构使用,标记 readonly 不改变运行行为;全量加 readonly
      // 要改 300 余个组件签名,无实际收益。该规则关闭不影响缺陷检出。
      "sonarjs/prefer-read-only-props": "off",
      // 判别联合/typeof 分支漏支返回 undefined 是静默错值,编译期收口
      "@typescript-eslint/switch-exhaustiveness-check": "error",
      // 已废弃 API 调用(React/浏览器/依赖)在类型层可见,继续用会随依赖升级断供
      "@typescript-eslint/no-deprecated": "error",
      // 调试残留不进产物;异常上报仍可用 warn/error
      "no-console": ["error", { allow: ["warn", "error"] }],
    },
  },
  // 测试代码专用规则:Testing Library 断言/查询的 await 与 act 用法、
  // jest-dom 首选写法;e2e 侧禁止 waitForTimeout、缺失 await 等。
  // 结构断言刻意直取 container/子节点,节点访问与命名约定两条纯风格规则关闭
  {
    files: ["src/**/*.{test,spec}.{ts,tsx}"],
    ...testingLibrary.configs["flat/react"],
    rules: {
      ...testingLibrary.configs["flat/react"].rules,
      "testing-library/render-result-naming-convention": "off",
      "testing-library/no-container": "off",
      "testing-library/no-node-access": "off",
    },
  },
  {
    files: ["e2e/**/*.ts"],
    ...playwright.configs["flat/recommended"],
  },
  eslintConfigPrettier,
);
