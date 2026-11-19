import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import jsxA11y from "eslint-plugin-jsx-a11y";
// 放在所有配置之后:只关掉与 prettier 冲突的格式规则,格式判断单一真源归 prettier
import eslintConfigPrettier from "eslint-config-prettier";

export default tseslint.config(
  { ignores: ["dist", "src/lib/api-types.gen.ts", "*.gen.ts"] },
  {
    extends: [
      js.configs.recommended,
      ...tseslint.configs.recommended,
      jsxA11y.flatConfigs.recommended,
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
  eslintConfigPrettier,
);
