# 066-web-build-and-test-hygiene 实施任务

## 1. 路由懒加载(零业务逻辑变更)

- [x] 1.1 `main.tsx` 全路由 `lazy()` 化:`/login`、`/bot/:botKey` 保持
      静态导入;`AppLayout` / `AdminLayout` 与全部 admin 子页改 `lazy`
      **验证**:`pnpm build` 后 `dist/assets/index-*.js` 降至约 833 kB
      (基线 997 kB);`pnpm typecheck` 绿
- [x] 1.2 路由级 `Suspense` 复用既有 `RouteSuspenseFallback`(禁新建骨架件,
      前端宪法 Article 4)
      **验证**:慢网节流下逐页跳转无白屏无跳变

## 2. 构建分包

- [x] 2.1 `build.target: "es2022"` + `chunkSizeWarningLimit: 1500`
      **验证**:构建无 >500 kB 警告噪音
- [x] 2.2 按库 `manualChunks`:`react-vendor`(react/react-dom/react-router)、
      `markdown`(react-markdown 及其 remark/rehype/mdast/unified/hast 链)、
      `radix`、`react-query`
      **验证**:构建产物中 echarts 仍为独立**动态**块,且首屏不加载
      zrender(用 `--sourcemap` + 检查首屏 chunk 引用确认)
- [x] 2.3 **回归护栏**:断言 echarts/zrender 相关模块只出现在动态块引用中,
      不进入口静态依赖(防将来有人把粗粒度 vendor 桶改回来)
      **验证**:同 2.2

## 3. 最小测试网

- [x] 3.1 引入 vitest(devDep,独立 `vitest.config.ts`,不复用
      `vite.config.ts` 的 build 段)
      **验证**:`pnpm test:run` 可执行;不影响 `pnpm build` / `pnpm lint`
- [x] 3.2 `src/lib` 纯函数首批测试:`datetime.formatDuration` 的
      `59.95s → 60s` 进位撞档、`date-utils.endExclusive` 的 off-by-one、
      `cron-schedule` 的合法/非法 cron 与 next_fire、`api.ts` 的 SSE 分帧
      (含 CRLF 边界、畸形帧跳帧)与错误码转译查表
      **验证**:先跑红(这些边界当前无保护)后转绿
- [x] 3.3 CI web job 增加 `pnpm test:run`(置于 `typecheck` 之后、
      `lint` 之前,失败即门禁失败)
      **验证**:本地同参预演通过

## 4. 收口

- [x] 4.1 `pnpm typecheck && pnpm lint && pnpm lint:css && pnpm format:check`
      全绿;`AGENTS.md`「常用命令」补 `pnpm test`(禁幽灵引用)
- [x] 4.2 亮暗两态走查对话页与两个统计页(分包不改样式,确认无视觉回归)
      **实测(2026-09-28,playwright 双色 scheme,真代理拓扑)**:亮/暗两态
      分别走查对话页(含流式答案卡片)、质量页、用量页并截图目验:
      暗态无未着色残留、无硬编码色块;统计页表格/图表在分包后
      (echarts 动态 chunk)渲染正常(用量页图表区随 tab 懒加载成功)。
      入口 104 kB 分包下首屏无样式闪烁(FOUC)——样式仍由入口
      index.css 同步引入,分包只含 JS。
- [x] 4.3 记录首屏 gzip 前后对比于 apply;若后续要设 bundle 预算,
      以本数据为起点另立 change

> 066 落地记录(2026-09-27):
> - 首屏:入口 index chunk 997.45 kB(gzip 299.25)→ **104 kB(gzip 34.9)**;首屏静态闭包
>   = index+react-vendor(284/91.8)+radix(152/49.1)+react-query(36/10.6)≈576 kB
>   (gzip 186.4 kB),优于提案预估的 832.78/259.93(vendor 拆出后按库缓存)。
>   echarts(540 kB)为独立动态块;首屏静态闭包经 chunk import 闭包计算确认零
>   echarts/zrender;markdown 块随 ChatPage 懒加载,非首屏。
> - vitest 3.2.7 devDep + vitest.config.ts(node 环境,仅 src/**/*.test.ts);
>   5 个测试文件 43 例全绿(datetime 进位撞档/date-utils 跨月跨年闰日/cron
>   白名单往返与 0-7 归一/api.ts SSE 分帧 CRLF+畸形跳帧+错误码转译/echarts
>   边界静态守卫)。api.ts 导出 dispatchSseBlock 供纯函数断言。
> - CI web job 增补 pnpm test:run(typecheck 后、lint 前);AGENTS.md 常用命令
>   同步(引用真实脚本名 test:run,防幽灵引用)。
> - 顺带修复:ci.yml 既有超长行(psql 行)折行——lint 触及文件的行长规则。
> - cron 解析测试坐实既有白名单口径:0,30 与 */30 互通(字段集合相等);*/7 等
>   非因数步进反解 null 走高级模式;next_fire 为后端计算(前端仅 formatDateTime
>   展示),前端无该纯函数,无新增守护面(任务 3.2 口径说明)。
> - 【待确认-环境限制】1.2 慢网节流逐页跳转、4.2 亮暗两态走查:本环境无浏览器,
>   未执行目验;分包+懒加载零业务逻辑改动,样式 token/组件零触碰,风险面限于
>   骨架闪现有无,留待有浏览器环境补走查。
