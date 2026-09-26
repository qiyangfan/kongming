# Design

## Context

现状是「桌面直写、窄屏兜底」:全仓 52 处断点前缀中业务列表页几乎为零,
admin 17 个表格页窄屏全部依赖 `ui/table` 的 `overflow-x-auto` 横滚;壳层
两处折降(app-layout 抽屉、AdminLayout 顶部横滚子导航)是仅有的移动适配。
全站无安全区处理(index.html 无 `viewport-fit=cover`、`env(safe-area-inset-*)`
零使用)、无触控基线(tap-highlight/overscroll 均未设置,行内小按钮热区
24–32px)。图表容器自适应依赖 echarts-for-react 的容器尺寸监听,天然可用。
UI 件全部经由 shadcn/ui 标准件(ui/ 目录),已有 Sheet/Dialog/DropdownMenu/
AlertDialog 可复用;零新增依赖(DataView 列模型自研轻量声明,初稿的
TanStack 方案实施期推翻,见 D3 修订)。动机与范围见
proposal.md,行为契约见
specs 增量(mobile-experience 新增、web-nav「应用壳结构/响应式外壳」修改)。

## Goals / Non-Goals

**Goals:**

- 断点书写方向反转为移动优先:无前缀基线即移动形态,`md:` 起恢复桌面形态,
  让窄屏成为每页的默认形态而非遗漏项。
- 移动信息架构:底部标签栏 + 管理层进 + 会话抽屉,替代抽屉复刻侧栏与横滚子导航。
- 列表/表单/触控三类横切形态经基类与标准件单点收编,17 个管理页逐页接入但
  不逐页手写移动布局。

**Non-Goals:**

- 见 proposal.md Non-goals(PWA/原生壳/后端契约/平板专属/新依赖/轻量页重设计/
  图表手势/桌面改版均不做)。设计层补充:不做服务端 UA 分流或独立移动路由树,
  仍是同一路由表上的响应式渲染。

## Decisions

### D1 断点语义:移动优先基线,`md:` 单一折叠点不变

无前缀类即移动形态,`md:` 前缀恢复桌面形态。与现状书写方向相反,但折叠点
仍是宪法既定的 `md` 一个,不引入 `sm/lg` 布局分叉。

- 替代方案「维持桌面优先、逐页补 `max-md:` 折降」否决:现状 17 页零折降
  正是这种写法的必然遗漏,补丁式折降会随新页面再次遗漏。
- `sm:` 仅保留非布局用途(对话框定宽档、统计卡栅格),不作为布局折叠点。

### D2 壳层三件套:BottomNav / 管理索引页 / 会话抽屉,分叉点收敛在 AppLayout

- `BottomNav`(新标准件):消费与宽屏侧栏同一 `visibleNav()` 单源(含 RBAC
  过滤),固定底部,高亮当前能力;页面内容区预留底部避让(导航高度 +
  安全区),由壳统一 padding,页面不自行感知。
- 管理层进:`/admin` 在窄屏渲染索引页(按权限列全部子页入口,数据与
  AdminLayout 的 ADMIN_NAV 同源),宽屏维持「重定向到默认子页 + 侧栏子导航」
  现状;子页顶部在窄屏出现返回入口。AdminLayout 现有横滚子导航移除。
- 会话抽屉:现 Sheet 承载保留,但内容改为仅会话列表(+新建),主导航区
  不再进入抽屉;入口从壳顶栏移到对话区页头。
- 替代方案「横滚 tab 条承载管理子页」否决:17 项入口横滚拇指不可达、
  无位置感;「底部 tab 直放管理子页」否决:tab 数超 5 且随权限变化。
- 依赖口径:shadcn/radix 生态无现成底部导航件,不为此引入第二套 UI Kit;
  BottomNav 是既有原语 + react-router `NavLink` 的组合件(组合不等于手搓
  基础件),语义与可达性交给 `<nav>` + 链接原生能力。

### D3 列表双形态标准件:轻量列声明,单组件双渲染(实施期修订)

新共享组件 `DataView` 采用自研轻量列声明(`DataViewColumn`:键/表头/渲染
函数/卡片优先级),`md+` 渲染现有 `ui/Table`,`md-` 以同一份列定义渲染卡片
列表:每行一卡,呈现卡片优先级最高的栏位,行操作收进 DropdownMenu(支持
行级可见性/禁用谓词与按行文案)。搜索/分页/筛选是页面级控件,保持在组件外,
不进标准件;排序/列显隐/虚拟化本期不做。

- **修订记录**:初稿曾选 `@tanstack/react-table` 为列模型底座,并以此否决
  手写方案;实施期裁决推翻——本仓 17 页列表均为「一次声明、双形态直渲染」
  的静态结构,用不到 row model/排序/列显隐等框架能力,引入 headless 表格库
  属超配(多一份依赖与概念面,换不来行为增量);spec 契约约束的是「一份列
  定义、两形态同源」,不约束实现载体。轻量声明不构成「自研表格框架」——
  框架性能力一个都没写。
- 替代方案「表格与卡片两个独立组件」否决:17 页双份字段描述必然漂移。
- 替代方案「同一 DOM 用 CSS 切换表格/卡片」否决:DOM 结构与语义不同,
  伪响应拖累可达性与包体。
- 结果:每页改造 = 把内联 `<Table>` 换成 `<DataView columns={…} rows={…}>`,
  栏位映射一次声明,两形态同源。

### D4 表单全屏化改在 `ui/dialog.tsx` 基类,页面零改动

`DialogContent` 基类调整:窄屏默认全屏(内容区滚动、Footer 固定底部),
`sm:` 起维持现「定宽居中」;页面级既有 `sm:max-w-*` 覆写无需变动。
`AlertDialog`(确认类)独立组件,不动,维持居中小卡。

- 替代方案「新增 FormSheet 组件逐页替换」否决:全站 15+ 页、60+ 处表单
  对话框,逐页替换工作量与遗漏风险都不可接受;基类单点收编符合宪法
  「同一覆盖 ≥3 处收编进组件默认值」。
- Footer 固定底部需处理表单过长:内容区独立滚动,Footer 常驻,提交键
  永远可达。

### D5 触控/安全区基线落在基类与全局层,不做页面级特判

- `index.html` viewport:`viewport-fit=cover` + `interactive-widget=resizes-content`
  (软键盘压缩布局视口,输入区随流抬升)。
- `index.css`:`@layer base` 补 `-webkit-tap-highlight-color: transparent`、
  滚动容器 `overscroll-behavior: contain`;新增安全区工具类(pb/pl/pr/pt
  四向 + 底部导航避让复合档 pb-safe-nav),供壳层内容列、BottomNav、
  portal 浮层、全屏表单 Footer 消费。
- 触控热区:button/toggle 基类加等效命中扩展(伪元素外扩,视觉尺寸不变),
  小尺寸档(icon-xs 等)视觉保留;密集行内操作的可达性由 D3 溢出菜单
  承接,不追求全站 44px 视觉尺寸(会破坏信息密度)。
- 文本输入防缩放:input/textarea 已有窄屏 `text-base`,补齐 select 同款。

### D6 问答结果表豁免卡片化(边界裁决)

问答结果是任意动态列,不存在可预知的「卡片字段映射」,卡片化不可行;
窄屏保留结果表横向滚动,并做行高触控适配(纵向滚动消息流不受横向手势
劫持)。卡片化契约(Spec「列表窄屏卡片化」)适用对象是**结构化管理/用户面
列表页**——两个 spec 条款各有明确主语,不冲突。

### D7 双仓标准件同步:本变更先改本仓

工作区 AGENTS.md 引用的聚合目录 `../FRONTEND-CONSTITUTION.md` 与
`../scripts/sync-frontend-standard.sh` 已随聚合目录重组不在原位
(`longzhong/` 下仅 jinguan/kongming/muniu 三仓)。本变更仅更新本仓
AGENTS.md 的响应式条款摘要;聚合宪法与同步脚本的恢复/改道由 owner
裁定,本 change 不臆造聚合目录文件。

## Risks / Trade-offs

- [Dialog 基类全屏化回归面大(全站表单)] → 逐页走查清单(tasks 内逐页勾选);
  基类改动保持「窄屏才分叉」,`sm:` 起样式零变化,桌面回归面趋近于零。
- [17 页收编遗漏] → tasks 逐页列清单,收口用 grep 核对 `pages/admin` 下
  不再存在裸 `<Table>` 直渲染的业务列表(标准件内部除外)。
- [BottomNav 遮挡页面底部内容] → 壳层统一预留避让高度,页面不自行计算;
  走查 375 档每个页面的底部可达性。
- [iOS 软键盘 + dvh 组合行为碎片化] → `interactive-widget=resizes-content`
  覆盖 Chrome 系;iOS Safari 走 `dvh` + 布局流输入区,真机走查兜底,
  不引入 visualViewport 自管滚动(复杂度不值)。
- [图表容器在布局切换时不重绘] → 图表容器不参与卡片/表格分叉(始终单列),
  风险仅剩转屏,echarts 容器监听已覆盖;走查兜底。

## Migration Plan

纯前端变更,随常规发布,无数据迁移;回滚即回退前端版本。实施顺序即
迁移顺序:基线(index.html/index.css/ui 基类)→ 壳(BottomNav/索引页/
会话抽屉)→ 标准件(DataView)→ 逐页收编 → 全站走查收口。基线先行
保证后续每页接入时触控/安全区已就绪。

## Open Questions

无——影响 spec、方案或任务拆分的疑点已全部裁决(D1–D7)。
