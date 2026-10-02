## Why

`ui/CardTitle` 基线渲染 `div`：卡片标题只有视觉密档，没有标题语义——读屏用户拿不到卡片级标题大纲，宪法 Article 7 的「页标题 ≥ 区块标题 ≥ 卡片标题」只活在注释里，组件层没有任何保证。缺语义的标准件逼着页面层手搓替代，且已经长出三种写法：muniu 控制台 Providers 两处改用原生 `h2` 承载 CardTitle 视觉配方并在注释里解释「CardTitle 基线是 div(kongming 同源)无标题语义」；muniu 登录卡用 `className="text-xl"` 密档把卡题压成页面级；jinguan/kongming 则是纯 div 零语义。同一诉求三种手搓，正是「优先用组件、避免手搓」要消灭的形态——正确解法是标准件把语义补上，而不是让每页各自用原生标签兜。jinguan 13 处、muniu 3 处、本仓 3 处消费方都在等这一下。

## What Changes

- `CardTitle` 增加 `as?: "h2" | "h3" | "h4"`，默认 `"h3"`，渲染真实标题元素；视觉配方（text-lg font-semibold leading-none tracking-tight）一字节不动，其余 props API 与 ref 类型不变（`HTMLHeadingElement`）。
- 默认层级依据：本仓与 jinguan 的页面标题由同源 `page-header.tsx` 以 h2 输出，卡片标题默认 h3 恒低于页标题；卡片位于区块标题（h3）之下时调用处传 `as="h4"`。
- 卡片标题即该页最高级标题的页面传 `as="h2"`：本仓三处认证入口（Login/ChangePassword/OAuthCallback，无 PageHeader）；muniu 登录卡与 Providers 两处区块头同样 `as="h2"`，两处原生 h2 手搓回归标准件（对齐在 muniu 仓执行，见 Non-goals）。
- 渲染守卫（`react-dom/server` 静态渲染，零新依赖）：默认输出 h3、`as` 各取值生效、视觉配方不回归。

## Non-goals

- 不改视觉配方、不改 `page-header` 的 h2、不动其他 `ui/` 标准件；`as` 只收标题层级，不收任意标签、不收 div（div 即本件要消灭的缺陷本体）。
- 不引入 jsdom / @testing-library：渲染守卫用 `react-dom/server` 静态渲染即可覆盖语义断言，不为一条守卫改构建面（muniu 2026-10-02 引入 testing-library 的组件测试路线不在本仓复制）。
- 不在本 change 内跨仓改码：jinguan/muniu 对齐按宪法 Article 5「语义级改动先发生在 kongming」待本仓落地后经 `scripts/sync-frontend-standard.sh` 执行，联合修改登记在聚合层 HANDOFF。
- 不加 `role="heading"` + `aria-level` 的补丁式方案：原生标题元素才是语义本体，ARIA 补丁是给不能改标签的场景用的，这里能改。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `web-nav`：应用壳骨架标准件新增「卡片标题输出标题语义」需求（标准件行为契约，跨认证入口与管理页同源）。

## Impact

- 本仓：`apps/web/src/components/ui/card.tsx`（+as 与标题元素渲染）、新增 `apps/web/src/lib/card-title-semantics.test.ts`（渲染守卫）、Login/ChangePassword/OAuthCallback 三页调用处 `as="h2"`。
- 跨仓（登记聚合层 HANDOFF，本仓不动码）：jinguan 13 处调用点（LoginPage 传 as="h2"，其余默认 h3）、muniu 3 处（登录卡改 as="h2" 去密档注释、Providers 两处原生 h2 回归 `<CardTitle as="h2">`、Overview 默认 h3）。
- 门禁：`make web-check`（tsc/eslint/stylelint/prettier/vitest）+ `vite build` 全绿；双仓 `ui/` 经 sync 脚本保持逐字节一致。
