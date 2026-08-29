# 040-theme-switch 设计

## Context

暗色令牌已全备(`index.css` 的 `.dark` 块,武侯祠夜配色),`@custom-variant dark (&:is(.dark *))` 已挂,全站组件一律走 token 类——切主题的唯一缺项是「挂/卸 `.dark` 类」这一层。既有可仿形态:`tenant-store.ts`(zustand + localStorage)。图表侧 `chart-view-impl` 在 useMemo 内经 `getComputedStyle` 读 token,实例不重挂则不跟随。动机与行为契约见 proposal 与 specs。

## Goals / Non-Goals

**Goals:**

- 单一可信的主题解析语义,首帧前生效(防 FOUC)。
- 切换入口、持久化、图表联动、公开页跟随,全部行为对齐 specs/theme-switch。

**Non-Goals:**

- 不做多主题注册机制(无 theme 对象体系,只有 `.dark` 有无两态)。
- 不做后端偏好存储;不引第三方主题库。

## Decisions

### D1 自建 ~40 行 theme-store,不引 next-themes

zustand store 仿 `tenant-store` 形态:`{ theme, setTheme }`,theme 为 `resolved` 结果(渲染用),另存手动偏好(`localStorage key: km-theme`,值 `light|dark`)。备选 next-themes(shadcn 生态惯用):引入新依赖只为 40 行可写逻辑,且其 Script 抽象对本仓 index.html 结构侵入——tech-stack 锁定纪律下倾向自建。

### D2 防 FOUC 用 index.html 内联脚本,解析语义与 store 钉死同契约

`<head>` 内阻塞式 4 行脚本:读 `km-theme` → 无则 `matchMedia('(prefers-color-scheme: dark)')` → 挂/不挂 `.dark` 到 `<html>`。备选 React effect 挂类:首帧晚于绘制,暗色用户必闪,否决。**双实现漂移风险**(内联 JS 与 store 各写一份解析)→ 契约钉死:同 key、同优先级(手动值 > 系统 > 亮色),两处注释互指,改动必须同步。

### D3 图表联动用 remount(key=主题),不做 echarts theme 注册

主题经 `useTheme()` 在 `chart-view` 壳感知,以 `key={theme}` 重挂实现组件,重建时 `getComputedStyle` 自然读到新 token;**重挂边界以下的本地状态会被清零**,故用户手动选定的图表类型(activeType)提升至壳持有。备选 echarts `registerTheme` 双主题:需把 token 色板复制成 JS 常量,违背「色值只经 token」铁律且与 CSS 变量失联;remount 一次 ~几十 ms。admin 用量/反馈两页各抽趋势图子组件(`UsageTrendChart`/`FeedbackTrendChart`)同机制——`key` 必须落在**含 token 派生 useMemo 的组件粒度**上,并经 build 验证懒加载边界(echarts chunk 独立于主包)。

### D4 系统偏好实时监听,仅无手动值时跟随

store 初始化时 `matchMedia.addEventListener('change')`:无手动值的会话内,用户改系统偏好即时跟随;有手动值则忽略。切系统主题不刷新页面的场景少,但实现成本一行,语义完整。

### D5 根元素挂 `<html>`(documentElement),配 `color-scheme`

`.dark` 挂 `html` 而非 body:Radix portal 挂 body(是 html 后代,`&:is(.dark *)` 命中,浮层主题正确);同时在 `.dark` 下置 `color-scheme: dark` 让滚动条/原生控件(日期、下拉)跟随,亮色同理写 `:root`。这是既有 `.dark` 块的一个增量,非新机制。

### D6 公开页只读跟随,不进 store 写路径

`BotPublicPage` 无用户语义,不渲染入口;防 FOUC 由 D2 内联脚本全站覆盖(它不分路由)。页内不引 store——脚本挂的类即最终态(访客偏好即系统偏好,无手动值来源)。

## Risks / Trade-offs

- [内联脚本与 store 解析语义漂移] → D2 契约钉死 + 双侧互指注释;解析顺序仅三步,漂移面小。
- [remount 闪帧] → 同 chunk 内重建,实测可感知度低;骨架兜异步 chunk 期(现已有)。
- [第三方代码块打印/复制场景色差] → `downloadPng` 已读 `--card`,随主题自然正确;无额外处理。
- [localStorage 禁用环境(隐私模式极端配置)] → 解析回退系统偏好,try/catch 包存取,功能降级不报错。

## Migration Plan

纯前端增量:新 store/入口/内联脚本 + chart-view 一处 key。无数据迁移、无契约变更。回滚 = 还原提交;localStorage 遗留 key 无害(无读取方即死数据)。

## Open Questions

(无——入口形态定为用户菜单内单项图标切换,已在 spec 场景钉死。)
