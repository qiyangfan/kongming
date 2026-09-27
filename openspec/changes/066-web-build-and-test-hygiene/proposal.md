# 066-web-build-and-test-hygiene 提案:前端分包与最小测试网

## Why

**首屏入口包 997.45 kB(gzip 299.25 kB)**,而 32 个路由里只有 `FeedbackPage` / `UsagePage` 两个走了 `lazy()`;其余 20+ 个管理页全部打进入口(已实测:工作流/触发器/团队/计费/助手/语义层页的独有文案均在入口 chunk 中)。

实测把全部 admin 路由改为懒加载后,入口降至 **832.78 kB(gzip 259.93 kB)**。但收益必须如实定位——拆解后**业务自身代码仅约 139 kB,其余约 700 kB 全为三方**(react-vendor 290 / markdown 链 149 / radix 110 / react-query 36 / lucide 图标约 46)。因此:

- 首屏 gzip 只降约 13%,**不承诺"瘦身数倍"**;
- 真实价值是两条:非管理用户从此不再下载约 100 kB 管理页代码;vendor 与业务分片后,业务发布不再打脏 700 kB 三方缓存。

**同时前端零测试**:31k 行 TS/TSX 无一个 `*.test.ts`、无 vitest 配置,而后端有 70% 覆盖率门禁 + 100+ 测试文件。仓内存在**已知边界、注释里写明了踩坑史**的纯函数:`datetime.formatDuration` 的 59.95s 进位撞档、`date-utils.endExclusive` 的 off-by-one、`cron-schedule` 的 cron 解析、`api.ts` 的 SSE 分帧与错误码转译——这些全部无任何自动化保护。

## What Changes

1. **全路由 `lazy()` 化**:`/login` 与公开嵌入入口 `/bot/:botKey` 保持静态(首屏直达),`AppLayout` / `AdminLayout` 及全部 admin 子页走 `lazy` + 路由级 `Suspense`(沿用仓内既有 `RouteSuspenseFallback` 范式,不新建骨架件)。
2. **构建参数**:`build.target = "es2022"`;`chunkSizeWarningLimit = 1500`;按库 `manualChunks`(`react-vendor` / `markdown` / `radix` / `react-query`)。
3. **坑位规避(实测踩过)**:`manualChunks` 禁止按"`node_modules` → 单一 vendor 桶"的粗粒度规则——那会把 zrender(约 250 kB,echarts 的渲染引擎)从**已正确懒加载**的 echarts 块提到首屏;必须以库名精确分包,echarts 相关整体留给 Rollup 自动共享块。
4. **vitest 接入(devDep)+ CI web job 增加 `test:run`**;测试范围严格限定 `src/lib/**` 纯函数,不引入 jsdom、不写组件测试。
5. echarts 懒加载块保持现状(550 kB 已在动态块,做法正确),不改。

## Non-goals

- **组件测试 / jsdom / Playwright E2E**:先不做。收益成本比不成立,纯函数网是最小可行第一步。
- **把 echarts 换成轻量图表库**:无质量问题不换依赖(ADR-001 技术栈锁定)。
- **bundle 大小 CI 硬门禁(预算阈值)**:阈值定多少需要数据支撑,不拍脑袋;先靠分包与观测。
- **`MarkdownText` 组件级 lazy**(可再省约 44 kB gzip):对话核心路径首屏即用,收益不成比例。
- **i18n**:中文自建产品无需求。
- **`esbuild.pure` 移除 console**:仓内已 0 处 `console.*`(已实测),做了等于空转。

## Capabilities

(无——构建与测试基础设施变更,不产生用户可见行为;同 `054-gates-env-parity` 形态,不新增 spec delta。)

## Impact

- **落点**:`apps/web/src/main.tsx`、`apps/web/vite.config.ts`、`apps/web/package.json`、`.github/workflows/ci.yml`、新增 `src/lib/**` 下若干 `*.test.ts`。
- **无契约 / schema 变更**;不触 ADR——前端样式宪法(Article 0–10)不受影响,分包不动样式纪律(token、组件复用、暗色一级公民等约束全部保持)。
- **已验证**:全量懒加载后入口 832.78 kB / gzip 259.93 kB(见 Why);改动仅构建配置与 import 形态,零业务逻辑变更。
