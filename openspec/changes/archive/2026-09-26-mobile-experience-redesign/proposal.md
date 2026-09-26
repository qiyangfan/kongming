# Proposal: mobile-experience-redesign(移动端全面重新设计)

## Why

当前移动端是「桌面布局 + 断点折降」的兜底产物:应用壳窄屏用抽屉复刻桌面侧栏,
管理面 17 个列表页在窄屏全部退化为横向滚动的多列表格,表单对话框按桌面定宽缩放,
全站无安全区/触控基线(viewport 无 viewport-fit=cover、`env(safe-area-inset-*)`
零使用、行内密集操作按钮热区过小)。横向滚动作为主要浏览手段、拇指不可达的
导航与操作位置,说明问题不在断点参数而在信息架构——移动端需要按移动场景
重新设计导航模型与呈现形态,而非把桌面端等比缩小。

## What Changes

- **移动导航模型重构**:`md` 断点以下放弃「汉堡按钮 + 抽屉复刻侧栏」,改为
  底部标签栏承载能力导航(对话/商店/门户/管理,沿用 RBAC 菜单守卫过滤);
  管理区窄屏改为层进式(索引页列全部子页入口 → 进入子页),取代顶部横滚子导航。
- **列表窄屏卡片化**:新增数据驱动的「表格/卡片双形态」共享标准件——桌面
  (`md+`)渲染表格,窄屏渲染卡片列表(呈现关键栏位子集);17 个管理列表页、
  门户/记忆等用户面列表页统一收编,行操作在窄屏收进溢出菜单。窄屏下不再以
  横向滚动作为列表浏览手段。
- **表单窄屏全屏承载**:新建/编辑类对话框表单在窄屏全屏化(顶部标题+关闭、
  内容滚动、底部动作栏),确认类 AlertDialog 维持居中小卡;经 `ui/dialog`
  基类单点收编,全站生效。
- **触控与安全区基线**:viewport 增加 `viewport-fit=cover` 与
  `interactive-widget=resizes-content`;新增安全区工具类并应用到底部标签栏、
  对话输入区等贴边区域;核心操作路径(底部导航/主提交按钮/卡片主操作)触控
  目标达到等效 44px 热区;补齐 tap-highlight、overscroll 等移动全局样式。
- **对话页移动体验**:输入区安全区避让与键盘行为适配,会话列表在窄屏经页头
  入口以抽屉唤出(保留现有抽屉组件);问答结果表窄屏保留横向滚动(动态任意
  列数据,区别于结构化管理列表),行高与触控适配。
- **规范与文档同步**:web-nav「响应式外壳」条款重写;新增 mobile-experience
  能力域 spec;工作区 AGENTS.md 前端宪法摘要的响应式条款(Article 9 执行摘要)
  同步重写为「移动优先」口径。

## Capabilities

### New Capabilities

- `mobile-experience`:移动端体验契约——窄屏导航模型(底部标签栏/管理层进)、
  列表卡片化、表单全屏承载、触控与安全区基线、对话页移动体验。窄屏视口下的
  呈现行为归本域立约,各既有能力域的功能行为(分页/搜索/CRUD/确认)不变。

### Modified Capabilities

- `web-nav`:「响应式外壳」条款重写——窄屏导航从「侧栏收抽屉、菜单按钮唤出」
  改为「底部标签栏 + 管理层进 + 会话抽屉」,宽屏侧栏常驻契约不变;应用壳其余
  条款(会话侧栏交互/RBAC 菜单守卫/路由守卫/租户切换)行为不变。

## Impact

- **代码**:全部集中在 `apps/web`(纯前端)——`index.html`、`src/index.css`、
  `components/`(app-layout、assistant-card、markdown-text、stream-card 修改;
  新增 bottom-nav、session-drawer、data-view、empty-state、error-alert,移除
  query-error)与 `components/ui/`(dialog、button、select、toggle 基类)、
  `pages/`(Chat/Login/ChangePassword/Store/Pay/Portal/Memory + admin 17 页 +
  AdminLayout + 新增 AdminIndexPage)。不触碰 `apps/api`、`apps/agent`、
  `contracts/`,OpenAPI 契约零变更(无需跑 `make contracts`)。
- **依赖**:DataView 列模型为自研轻量声明(键/表头/渲染函数/卡片优先级),
  **零新增依赖**——初稿曾放行 `@tanstack/react-table`,实施期裁决推翻
  (见 design.md D3 修订);UI Kit 零更换,全部复用既有 shadcn/ui 的
  Sheet/Dialog/DropdownMenu 与 Tailwind v4 断点,符合 docs/tech-stack.md
  技术栈锁定与 ADR-001 否决清单。
- **文档/规范**:openspec specs(web-nav 修改、mobile-experience 新增)、
  工作区 AGENTS.md 响应式条款重写。
- **回归面**:`ui/dialog` 基类窄屏全屏化影响全站表单,需全站走查兜底;
  17 个管理页逐页改造量大但由同一标准件承载,模式统一。

## Non-goals(不做什么)

- **不做 PWA/离线**:不引入 manifest、Service Worker、安装提示、推送。
- **不做原生壳或独立移动站**:不引入 React Native/混合壳/独立 m 站,仍是同一 SPA。
- **不改后端与契约**:API、schema、OpenAPI 契约零变更;不涉及
  ADR-002(信任边界)/003(认证)/004(schema)/005(契约管线)范围。
- **不做平板专属布局**:768px 及以上维持现行桌面形态,不设计平板双栏等专属版式。
- **不引入第二套 UI Kit,也不引入 headless 表格库**:不引入 MUI/AntD/vaul 等
  整套组件库或第三方抽屉件,基础交互件仍一律 shadcn/ui;DataView 列模型采用
  自研轻量声明(键/表头/渲染函数/卡片优先级,≈40 行类型 + 双渲染),本仓
  列表无排序/列显隐/虚拟化需求,不构成「自研表格框架」(框架性能力一个
  没写;原案 TanStack 方案实施期推翻,见 design.md D3 修订)。
- **轻量页不做移动专属重设计**:Login/PayPage/BotPublicPage/OAuthCallback/
  改密页等天然窄容器页仅收编全局基线(安全区/触控),不重排信息架构。
- **图表不做移动手势定制**:维持现有容器自适应与轴标签防重叠,不做捏合缩放
  等触控图表交互。
- **不改桌面端视觉**:桌面(`md+`)信息架构与视觉保持现状,仅接受标准件单点
  重构带来的最小回归面。
