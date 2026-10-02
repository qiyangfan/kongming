# 设计：CardTitle 标题语义与层级默认值

## D1 为什么是真实标题元素，不是 ARIA 补丁

组件渲染 `div` + `role="heading"` + `aria-level` 也能让读屏认出标题，但那是给「标签改不了」的场景准备的补丁。这里是自有标准件，标签改得动——原生标题元素是语义本体，顺带给键盘/浏览器查找（页面内标题查找、阅读模式）全部生效。ARIA 补丁方案否决。

## D2 默认层级：h3，且只收标题三档

- 本仓与 jinguan 的页面标题由同源 `page-header.tsx` 以 **h2** 输出（宪法 Article 7：页标题 ≥ 区块标题 ≥ 卡片标题）。卡片标题默认 **h3**，恒低于页标题；jinguan 13 处调用点绝大多数就在 PageHeader 页面内，默认值即正确值。
- `as` 取值域钉死 `"h2" | "h3" | "h4"`：h1 不收（本仓无 h1 惯例，页面标题 h2 即最高级，收 h1 会诱发页面标题层级漂移）；div 不收（缺陷本体）；h5/h6 不收（本仓无此深度嵌套，收之即过度设计）。
- 两张页面形态的偏离路径：卡片标题即页面最高级标题（认证入口页）→ `as="h2"`；卡片位于区块标题（h3）之下 → `as="h4"`。两处都是调用处一行声明，不为单页面开新组件。

## D3 实现形态

```tsx
const CardTitle = React.forwardRef<
  HTMLHeadingElement,
  React.HTMLAttributes<HTMLHeadingElement> & { as?: "h2" | "h3" | "h4" }
>(({ as: Component = "h3", className, ...props }, ref) => (
  <Component ref={ref} className={cn("text-lg font-semibold leading-none tracking-tight", className)} {...props} />
));
```

多态标签 + 同一 `HTMLAttributes<HTMLHeadingElement>`（h2/h3/h4 同型，不需要分布式联合类型的复杂度）；视觉配方一字节不动，回归面只有标签名。

## D4 守卫：react-dom/server，零新依赖

`react-dom/server` 的 `renderToStaticMarkup` 在 vitest node 环境直接可用（react-dom 已在依赖树），不需要 jsdom/@testing-library：

- 默认渲染 `<CardTitle>` → `<h3 class="…">`；
- `as="h2"` / `as="h4"` → 对应标签；
- 视觉配方 className 与改前一致（防「顺手美化」混入）。

静态源码断言（读 `card.tsx` 文本）不作为主守卫——它断言的是实现细节而非行为；行为断言以渲染结果为准。

## D5 跨仓对齐计划（本仓不动码，登记聚合层）

1. 本仓落地并归档后，`scripts/sync-frontend-standard.sh` 以 kongming 为源同步 `ui/` 到 jinguan/muniu（cmp 逐字节）。
2. jinguan：13 处调用点（LoginPage + Keys/Topology×3/Payments×2/Wallet×4/Pricing×2），仅 LoginPage 传 `as="h2"`，其余默认。
3. muniu：3 处收敛——App 登录卡 `as="h2"`（密档覆写与「压过 CardTitle 默认」注释随之删除，页面级表达改由 `as` 显式承担）；Providers 两处原生 h2 回归 `<CardTitle as="h2">`（配方回到标准件，注释删除）；Overview 默认 h3。
4. 以上联合修改登记 `longzhong/HANDOFF.md`；两仓对齐后各自 `make check` + 浏览器标题大纲抽查（读屏 heading 列表或 axe heading-order 规则）。
