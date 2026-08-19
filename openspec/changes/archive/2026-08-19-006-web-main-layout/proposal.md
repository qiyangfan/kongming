# 006 — 前端主布局:会话侧栏 + 对话区 + RBAC 菜单守卫

## Why

005 已交付端到端问答链路(`/chat/query` 契约就绪、真 LLM 冒烟五条路径全过),
但前端仍是认证闭环占位页——用户无法通过界面发起问答、看不到历史会话,
产品的"对话即主体验"尚未成立。本变更是 roadmap 第一梯队收尾的下一棒:
交付主布局,让数据问答从 curl 走向可用产品。

布局按品牌定位「全能 AI Agent 助手」(docs/brand.md)设计:对话式统一入口 +
侧栏为未来能力域(知识库/数据源/应用)预留配置驱动的伸展结构,
数据问答为首发场景填充对话区——而非纯 BI 工具的窄形态。

## What Changes

- **api(chat 域新增三个用户面端点,支撑侧栏与历史回显——chat spec 已立约
  行为的对齐实现)**:
  - `GET /chat/sessions`:当前用户 + 当前租户的会话列表,按 updated_at 倒序;
  - `GET /chat/sessions/{session_id}/messages`:会话消息完整回显
    (user 问句 + assistant 结构化结果);
  - `DELETE /chat/sessions/{session_id}`:删除会话(级联删消息)。
  - 三端点统一:JWT + 权限点 `chat:execute` + 租户白名单(X-Tenant-Id)+
    user+tenant 隔离(他人的会话一律 404,不区分不存在与无权)。
- **web(主布局落地)**:
  - 应用壳:侧栏(品牌区 + 新对话 + 会话列表 + 导航区 + 用户区)+ 对话区,
    响应式(md 以下侧栏收为抽屉);
  - 会话侧栏:列表/新建/切换/删除,刷新后恢复当前会话;
  - 对话区:历史消息回显、提问框、结果渲染(摘要/表格/SQL 折叠/洞察/统计),
    图表按响应 chart 建议渲染(bar/line/pie),echarts 主题色从设计 token 派生;
  - RBAC 菜单守卫:导航配置驱动,菜单项声明所需权限点,无权限不显示;
    路由级守卫对直捣 URL 返回 403 页(不泄露管理面存在性以外的信息);
  - 租户切换器:当前租户经请求头注入所有 chat 请求(admin 已绑
    default/sakila/olist 三租户,切换即换数据域)。
- **契约**:api 新增端点后重导出 openapi.json 并再生成 web 类型
  (ADR-005 管线,禁手写)。

## Capabilities

### New Capabilities
- `web-nav`:前端主布局域行为契约——应用壳结构、会话侧栏交互、RBAC 菜单守卫、
  路由守卫(401/403/首登改密分流)、租户切换、响应式断点
  (roadmap D 层钦定「web 主布局域开工时随行立 spec」)。

### Modified Capabilities
- `chat`:新增「会话列表与消息历史端点」Requirement——列表排序/隔离语义/
  删除级联/消息回显结构;既有「会话持久化」Requirement 的侧栏场景
  由此获得实现通路,不改动已立约条款。

## Non-goals(本变更不做)

- **RBAC 管理面 CRUD 页面**(用户/角色/权限/部门管理,roadmap 梯队 2,
  仅预留菜单入口与 403 守卫机制);
- **结果导出 CSV/Excel 与 EXPLAIN 执行计划**(chat spec 已立约,但后端端点
  未立,随后续变更;本次不新增半截契约);
- **图表 scatter 类型与 PNG 下载**(chart 建议推断纯函数只产 line/pie/bar/none,
  scatter 无数据来源,随图表域深化);
- **消息反馈(点赞/纠错)与流式输出**(feedback ×3 与 query-streaming 域,远期);
- **会话重命名/搜索/置顶**(侧栏交互最小集:列表/新建/切换/删除);
- **深浅色主题切换器**(dark token 已备,切换交互随品牌动效域);
- **动态数据源选择器**(datasource 列表端点属动态数据源变更,本次请求体
  仍走缺省 default)。

## Impact

- **代码**:
  - `apps/api/kongming/chat/`(router/service/repository/models 增会话查询与删除);
  - `apps/web/src/`(路由重构、AppLayout/侧栏/对话区/守卫/租户 store、
    shadcn 组件按需补充);
  - `contracts/openapi.json` + `apps/web/src/lib/api-types.gen.ts` 重生成。
- **契约**(ADR-005):新增三个用户面端点,响应模型
  SessionItemOut/SessionMessageOut 等;改 api 后必跑 `make contracts`。
- **schema**:无新表/改表(chat_session/chat_message 列已一次建全,ADR-004;
  删除走 ON DELETE CASCADE 既有约束,无迁移)。
- **认证/授权**(ADR-003):复用既有 JWT + require_perm + 租户白名单机制,
  不新增权限点(chat:execute 已随 005 播种);会话隔离属安全规则类,
  **测试先行**。
- **信任边界**(ADR-002):不涉 agent 面;新端点只读控制面自有表 + 删除,
  不触碰 `/internal/*` 与数据源执行。
- **依赖**:web 已具备 react-query/zustand/echarts/react-router 7,预期零新增;
  如需补充 shadcn 组件均为 dev 内部产物,不引入新库(tech-stack 锁定)。
