# 实施任务

每项 ≤ 2h；守卫先红后绿。验证证据见 review.md。

## 1. 守卫先行（先红）

- [x] 1.1 新增 `apps/web/src/lib/card-title-semantics.test.ts`（react-dom/server 静态渲染）：默认 `<CardTitle>` 输出 h3；`as="h2"`/`as="h4"` 输出对应标签；视觉配方 className 与改前逐字节一致。
- [x] 1.2 红灯实证：当前 CardTitle 渲染 div，1.1 默认层级断言必红（记录报错文本）。

## 2. 实施

- [x] 2.1 `ui/card.tsx`：CardTitle 增加 `as?: "h2" | "h3" | "h4"`（默认 h3），ref 类型改 `HTMLHeadingElement`，视觉配方不动。
- [x] 2.2 Login/ChangePassword/OAuthCallback 三页调用处补 `as="h2"`（卡片标题即该页最高级标题）。
- [x] 2.3 复核全仓 CardTitle 调用点（当前 3 处）无遗漏、无页面层原生标题手搓卡题配方。

## 3. 验证

- [x] 3.1 `make web-check` 全绿（tsc/eslint/stylelint/prettier/vitest run，含 1.1 守卫）。
- [x] 3.2 `vite build` 产物抽验登录页卡题为 h2 元素。
- [x] 3.3 浏览器标题大纲抽查：三个认证入口页 heading 列表含页面级标题（读屏/axe heading-order 无跳过告警）。

## 4. 跨仓对齐与收尾（本仓只拟计划不执行）

- [x] 4.1 聚合层 `longzhong/HANDOFF.md` 登记联合修改：jinguan 13 处/muniu 3 处的对齐清单与 sync 脚本执行顺序（design.md D5）。
- [x] 4.2 主规格 sync 进 `web-nav` 后归档（delta 已随提案写好，`--skip-specs` 纪律适用时按本仓 HANDOFF「归档前 specs 已手工同步」条执行）。
- [ ] 4.3 两仓对齐完成后，聚合层 HANDOFF 回写「已对齐 + 门禁证据」。
