# 提案核验与实施验证

提案阶段核验(2026-10-02):

- 语义方案(原生标题元素 vs ARIA 补丁)、默认层级(h3,恒低于 page-header 的 h2)、
  `as` 取值域(只收 h2/h3/h4,不收 h1/div/h5/h6)已经 D1/D2 裁决,与宪法 Article 5/7 同源。
- 跨仓对齐(jinguan 13 处 / muniu 3 处)不在本仓执行,联合修改登记聚合层 HANDOFF。

## 实施验证(2026-10-02,任务 1–3 全项)

- **守卫先红后绿(1.1/1.2)**:`src/lib/card-title-semantics.test.ts`(react-dom/server
  `renderToStaticMarkup`,零新依赖,`.test.ts` 内以 createElement 书写——vitest 网只收
  `.ts`,不为守卫改构建面)。红灯实证:改前 5/5 失败,默认层级断言报
  `expected '<div class="…">' to be '<h3 …>'`,且 `as` 以非法属性漏到 DOM
  (`as="h3"`);实施后 5/5 绿(默认 h3、as=h2/h4 生效、配方打底不丢、props 透传)。
- **实施面(2.1/2.2/2.3)**:`ui/card.tsx` 多态标题元素(默认 h3,ref 改
  `HTMLHeadingElement`,视觉配方 className 一字节未动);Login/ChangePassword/OAuthCallback
  三处认证入口 `as="h2"`。全仓复核:grep `<CardTitle` 恰 3 处调用点;卡题配方类
  (`leading-none tracking-tight`)页面层零手搓;`CardHeader` 使用方全部同用 `CardTitle`,
  无裸标题顶替形态。
- **门禁(3.1)**:`make web-check` 退出码 0(tsc/eslint/stylelint/prettier 全序);
  `pnpm test:run` 86/86(13 文件,既有 81 + 本守卫 5)。
- **产物抽验(3.2)**:`vite build` 成功;压缩产物内 CardTitle 实现为
  `({as:e="h3",…})=>jsx(e,{className:"text-lg font-semibold leading-none tracking-tight",…})`
  (默认 h3 + 配方逐字节在产物),登录页调用点 `as:"h2",children:"孔明 Kongming"` 接线在产物。
- **浏览器标题大纲(3.3)**:真 api(原生 18000,迁移位 0036=head)+ web dev
  (`VITE_API_PROXY_TARGET` 真代理,5199)+ playwright-core 系统 Chrome headless:
  /login、/auth/dingtalk/callback(无参错误态)、/change-password(smoke087 UI 登录后)
  三页 heading 大纲均为单条页面级 `h2`(孔明 Kongming / 第三方登录 / 修改密码),
  连续层级无跳档(h2→h4 类),截图留证 `/tmp/km-heading-check/`。视觉配方经守卫
  逐字节钉死,未另做像素走查。
- **规格 sync(4.2)**:delta ADDED Requirement「卡片标题输出标题语义」原文并入
  `openspec/specs/web-nav/spec.md`;`openspec validate web-nav --type spec --strict`
  单域通过(仅一条 >500 字符 INFO 级提示,与 workflow-orchestration 既有形态一致);
  全量 strict 仍 33/45,12 项失败域与 HANDOFF 冻结清单一致(ai-eval/audit/chat/
  datascope/datasource/eval/knowledge/multitenant/nl2sql/rbac/runtime-resilience/
  web-admin),本件未新增失败项。归档按「specs 已手工同步」纪律走 `--skip-specs`。

## 遗留(非本件缺陷)

- 任务 4.3(两仓对齐后聚合层 HANDOFF 回写「已对齐+门禁证据」)按 D5 条件触发:
  待 jinguan(13 处)/muniu(3 处)经 sync 脚本对齐并各自过门禁后回写,本仓不再动码。
