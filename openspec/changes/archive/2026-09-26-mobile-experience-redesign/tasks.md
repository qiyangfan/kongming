# Tasks

验证基线:apps/web 下 `pnpm typecheck && pnpm lint` 全绿;涉及走查的任务按
375/768/1280 三档 + 亮暗两态执行(桌面档只为确认零回归)。

## 1. 全局基线(index.html / index.css / ui 基类)

- [x] 1.1.index.html viewport 补 `viewport-fit=cover` 与
  `interactive-widget=resizes-content`;index.css `@layer base` 补
  tap-highlight 置空与滚动容器 overscroll 隔离,并新增安全区工具类
  (pb/pl/pr/pt 四向 + 底部导航避让复合档 pb-safe-nav)。验证:375 档模拟
  全面屏(区域含 Home 指示条)页脚贴边元素不再被遮挡;tsc/lint 绿。
- [x] 1.2.ui/button 与 ui/toggle 基类加等效命中扩展(伪元素外扩,视觉尺寸
  不变),icon-xs 等小档触控热区达到 44px 等效;ui/select 窄屏补
  `text-base` 防聚焦缩放。验证:开发工具命中测试小按钮热区 ≥44px;
  375 档聚焦 select 页面不缩放;tsc/lint 绿。
- [x] 1.3.ui/dialog 基类窄屏全屏化:DialogContent 无前缀基线改为全屏
  (内容区滚动、Footer 固定底部、消费安全区),`sm:` 起维持现定宽居中;
  AlertDialog 维持居中小卡不动;页面级 `sm:max-w-*` 覆写逐页核对无需调整。
  验证:UsersPage 新建/编辑表单 375 档全屏且提交按钮常驻可见、1280 档
  定宽居中无变化;确认删除弹窗两档均居中小卡;tsc/lint 绿。

## 2. 壳层(BottomNav / 管理层进 / 会话抽屉)

- [x] 2.1.新建 BottomNav 标准件:消费 `visibleNav()` 同源权限过滤、固定
  底部、当前能力高亮、消费安全区;AppLayout 窄屏渲染 BottomNav 并为内容
  区统一预留底部避让高度,移除现壳顶栏汉堡入口。验证:375 档底部标签栏
  呈现且权限过滤与宽屏侧栏一致(无 portal 权限账号不出现门户)、当前路由
  高亮正确;tsc/lint 绿。
- [x] 2.2.管理层进:`/admin` 窄屏渲染索引页(与 ADMIN_NAV 同源、按权限列
  全部子页入口),宽屏维持重定向现状;AdminLayout 移除横滚子导航,子页
  窄屏顶部出现返回索引入口。验证:375 档直达 /admin 呈索引页、进入子页
  可返回;1280 档 /admin 仍重定向且侧栏子导航不变;tsc/lint 绿。
- [x] 2.3.会话抽屉改造:Sheet 保留但内容改为仅会话列表 + 新建对话(主导航
  不再进抽屉),入口移至对话区页头;抽屉内新建/切换/删除行为与宽屏侧栏
  一致。验证:375 档经页头唤出抽屉完成新建/切换/删除全链路,刷新恢复
  当前会话;tsc/lint 绿。

## 3. 列表双形态标准件(DataView)

- [x] 3.1.新建 DataView 共享组件(**零新增依赖**;初稿 TanStack 方案实施期
  推翻,见 design.md D3 修订):自研轻量列模型单源(`DataViewColumn`:键/
  表头/cell 渲染函数,卡片优先级经 cardPriority 声明),`md+` 渲染 ui/Table,
  `md-` 以同一份列定义渲染卡片列表,行操作收 DropdownMenu(行级可见性/
  禁用谓词、按行文案),破坏性操作保留确认链路;空态/加载态复用 EmptyState。
  验证:17 个管理列表页 + 门户/记忆页接入,375 档卡片形态、1280 档表格
  形态同源呈现;tsc/lint 绿。

## 4. 用户面页面收编

- [x] 4.1.ChatPage 移动体验:输入区消费安全区工具类;结果表卡片内横滚与
  纵向滚动手势隔离、行高触控适配;消息流/流式卡/结果卡 375 档单列核查。
  验证:375 档聚焦输入框软键盘不遮挡输入区,流式问答全链路可完成,
  6 列结果表可横滚看全列;tsc/lint 绿。
- [x] 4.2.PortalPage 两张列表(订阅订单/用量)接 DataView;MemoryPage
  记忆列表接 DataView、底部添加表单窄屏折行(去除固定 w-44 下拉)。
  验证:两页 375 档卡片形态无横向滚动,1280 档表格形态不变;tsc/lint 绿。
- [x] 4.3.StorePage 核对(栅格已移动优先):工具行窄屏折行、卡片触控热区、
  详情表单吃 1.3 全屏基线。验证:375/1280 两档走查无回归;tsc/lint 绿。

## 5. 管理页逐页收编(DataView 接入)

- [x] 5.1.批 A(核心单表):UsersPage、RolesPage、DatasourcesPage 接
  DataView(状态列行内启停在窄屏收进卡片、宽屏保留);每页栏位映射与
  操作菜单完成。验证:三页 375 档卡片化无横滚、启停/编辑/删除链路可用,
  1280 档表格与行内操作不变;tsc/lint 绿。
- [x] 5.2.批 B(多表页):AuditPage(双表)、UsagePage(双表)、
  BillingPage(三表)、FeedbackPage 接 DataView(Tab 切换结构保留)。
  验证:四页全部表格 375 档卡片化,Tab 切换正常;1280 档不变;tsc/lint 绿。
- [x] 5.3.批 C(其余单/双表):RagPage、GuardrailsPage、ToolsPage、
  SemanticPage、McpPage、WorkflowsPage、TriggersPage、TeamsPage、BotsPage
  接 DataView。验证:九页 375 档卡片化、上传/扫描/重建索引等操作链路
  可用;1280 档不变;tsc/lint 绿。
- [x] 5.4.DeptsPage 特例(树形非表格):窄屏卡片化树形(缩进层级保留、
  行操作收溢出菜单)。验证:375 档树形可读可操作、增删改链路可用;
  1280 档不变;tsc/lint 绿。

## 6. 收口(规范文档 / 全站走查 / 门禁)

- [x] 6.1.重写工作区 AGENTS.md 前端宪法摘要的响应式条款:移动优先书写
  方向、md 单折叠点、BottomNav/DataView/全屏表单三标准件、375 验收档;
  备注 D7(聚合目录缺失,双仓同步待 owner 裁定)。验证:文档口径与
  specs/ 实现一致,旧「Sheet 抽屉范式/表格横滚」表述清除。
- [x] 6.2.全站走查收口:核心路径冒烟(登录 → 对话问答全链路 → 商店 →
  门户 → 管理 CRUD)在 375/768/1280 三档 + 亮暗两态执行;底部导航遮挡、
  安全区、键盘行为专项核查。验证:走查记录落入 apply 记录,无 P1 及以上
  遗留。
- [x] 6.3.grep 收口核对:`pages/admin` 与用户面列表页不存在裸 `<Table>`
  业务直渲染(DataView 内部除外)、`overflow-x-auto` 仅存于
  ui/table 基类/代码块/问答结果表等豁免位;`pnpm typecheck && pnpm lint`
  终验。验证:核对清单与命令输出落入 apply 记录。
