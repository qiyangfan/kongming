# 006 design — 前端主布局:会话侧栏 + 对话区 + RBAC 菜单守卫

## Context

- 005 已交付 `POST /chat/query`(JWT + chat:execute + 租户白名单),但用户面
  **只有这一个问答端点**——chat spec「会话列表侧栏/刷新恢复历史」立约在前,
  实现通路在后,本变更补齐三个只读+删除端点;前端仍为认证闭环占位页。
- 既有资产:`chat_session(user_id, tenant_id, updated_at DESC 索引正好支撑列表)`、
  `chat_message(ON DELETE CASCADE,级联删除白拿)`;router 已有
  `require_perm("chat:execute")`/`current_user`/`X-Tenant-Id` Header 依赖链;
  web 已有 react-query 5 / zustand / echarts / react-router 7,shadcn 已初始化
  (现仅 button/card/input/label 四件)。
- 005 落库事实(决定回显设计):assistant 消息 `result_json` 存
  `{columns, rows, insights, stats, chartType}`——**不含 x/y 轴列名,
  也不含 REJECTED 分态文案(error)**;REJECTED/FAILED 轮 result_json 为空。

## Goals / Non-Goals

**Goals:**
- 「对话即主体验」成立:打开即问、历史可回、会话可管、租户可切;
- 信息架构按「全能 AI 助手」伸展:导航配置驱动,新能力域入口零改壳代码;
- 会话隔离安全语义测试先行(越权 404 统一口径)。

**Non-Goals(design 级,补充 proposal)**
- 不做服务端会话标题改写(沿用首问 30 字符截断,LLM 命名随 prompt 域);
- 不做消息级缓存/SWA(react-query 内存缓存即可,刷新重拉);
- 不做 echarts 按需引入优化(体积随性能域);

## Decisions

### D1 契约形态:三个端点 + 回显模型

- `GET /chat/sessions?limit=50` → `[{id, title, updated_at}]`(倒序;limit
  上限 200,防全表拖库)。复用 `require_perm("chat:execute")` + `current_user`
  + `X-Tenant-Id` 依赖链,service 层复用既有租户解析
  (未绑定 403 / 缺省默认租户)。
- `GET /chat/sessions/{session_id}/messages` → `SessionMessageOut[]`(升序):
  `{id, role, question, summary, sql, status, error, result}`,
  `result` 仅 assistant 消息携带
  `{columns, rows, insights, stats, chart{type,x,y}}`。
- `DELETE /chat/sessions/{session_id}` → 204;归属校验同 query 端点
  (`find_session_owned`:不存在/非本人/跨租户统一 SessionNotFound → 404)。
- **备选否决**:消息端点并入列表端点(会话多时首屏过重);
  游标分页(侧栏场景 offset 足够,列表 limit 已限)。

### D2 回显补全:error/chart 完整化的两条路径

落库侧**增量补存**:`result_json` 增写 `error`(REJECTED/FAILED 分态文案)
与 `chart` 完整对象(替代裸 `chartType`)——纯加字段,向后兼容。
回显侧**优先读档,存量兜底重算**:005 冒烟的存量消息无 x/y/error 时,
用 `suggest_chart(columns, rows)` 纯函数确定性重算补全(落库输入=原始输入,
重算=原始输出),error 缺失则回 null 由前端通用文案兜底。
- 备选否决:迁移刷历史数据(为几条冒烟记录写迁移不值);只重算不补存
  (重算依赖函数未来不变,读档才是稳定事实源)。

### D3 删除语义:硬删 + 级联

`ON DELETE CASCADE` 既有约束直接生效;软删仅 identity 域(宪法 Article VI),
chat 域不引入软删列。重复删除第二次 404(已不存在),与归属校验口径天然统一。

### D4 前端路由:URL 即会话状态

- `/login` `/change-password` `/auth/:provider/callback` 不动;
- `/` 重定向 `/chat`;`/chat` = 新对话初始态;`/chat/:sessionId` = 具体会话
  (URL 是会话选择的单源:刷新/收藏/直达天然成立,localStorage 只存租户);
- `/admin/*` 占位路由组(本次仅 403 守卫机制与空壳页,RBAC CRUD 下一棒);
- `*` → 404 页。
- **备选否决**:store 态管理当前会话(刷新恢复要另写持久化,直达链接丢失)。

### D5 租户上下文:localStorage 单源 + 请求层统一注入

租户 key 持久化 `km.tenant`(与 `km.refresh` 同层);`api.ts` 每请求统一读并
注入 `X-Tenant-Id`(auth 端点多带该头无语义影响,单点注入免遗漏)。
切换动作 = 写 localStorage + `queryClient.invalidateQueries()`(键含租户维度)
+ 导航 `/chat`(会话选择跨租户重置,spec 立约)。默认租户取 `MeOut.tenants`
首项(无持久值时)。

### D6 RBAC 守卫:配置单源 + 谓词 + 双层守卫

- `lib/nav.ts`:导航项配置 `{id, label, icon, to, perm}` 单源——侧栏菜单与
  路由守卫同源消费,菜单过滤(`perm ∈ MeOut.perm_codes`)与路由守卫不会漂移;
- `hasPerm(user, code)` 谓词 + `<PermRoute perm>`(无权限渲染 403 页);
- 既有 `<Protected>`(认证/首登改密分流)不动,PermRoute 组合其上;
- 管理入口 perm 取 `system:user:manage`(角色/部门管理随下一棒并入同组)。

### D7 会话/消息数据层:react-query 键含租户

`useQuery(['sessions', tenantKey])` / `useQuery(['messages', sessionId, tenantKey])`;
提问为 mutation,onSuccess invalidate sessions(标题/updated_at 变)+ messages。
防重复提交 = mutation `isPending` 锁。

### D8 图表:echarts + token 派生色板

按 `chart.type` 渲染 line/pie/bar(none 不渲染);色板经
`getComputedStyle` 读 `--primary/--ring/--accent-foreground` 等 token 派生
5 色序列(主色打底、鎏金点睛),严禁硬编码十六进制(铁律 6)。
x/y 列名来自回显模型(D2 读档/重算)。

### D9 组件与响应式:shadcn 补件 + md 断点抽屉

新补 shadcn 组件:sheet(移动抽屉)、dropdown-menu(租户/用户菜单)、
alert-dialog(删除确认)、scroll-area(侧栏/消息列表)、avatar、separator、
skeleton(加载态)——全部经 shadcn CLI 落地,不手搓裸元素(铁律)。
`md:` 以下侧栏收抽屉;对话区/表格单列自适应;消息表格宽结果横向滚动
(`overflow-x-auto`),不做虚拟滚动(默认 1000 行截断在执行路由兜底)。

### D10 提问链路:复用 /chat/query,请求体最小

前端不传 history/persist(后端按 sessionId 补全,chat spec 已约);
`session_id` 取路由参数(新对话时缺省)。loading 态=提问按钮 spinner +
消息区占位(无流式,120s 超时由后端兜底,前端 fetch 无自定义超时)。

## Risks / Trade-offs

- [suggest_chart 未来演进导致重算≠原始建议] → 存量重算仅覆盖 005 冒烟几条;
  新消息一律读档(D2),演进无影响。
- [X-Tenant-Id 泄漏到不该带的请求] → 该头仅 chat 系端点消费,多带无语义;
  真正的风险是**忘带**,单点注入(`api.ts`)正是为此。
- [echarts 全量包体积] → 已是既有依赖现状,不恶化;按需引入随性能域。
- [删除会话与进行中提问竞态] → 删除入口仅在侧栏,提问中删除后 mutation
  返回的 invalidate 对不存在键无害(react-query 空转)。
- [会话列表无上限增长] → limit=50 缺省 + 200 上限;分页交互(加载更多)
  随侧栏深化,首版不做。
- [存量 REJECTED 消息 error 缺失] → 前端对 REJECTED 且无 error 呈通用拦截文案。

## Migration Plan

部署序:api 先行(新端点只读+删除,不影响既有路径)→ `make contracts`
重生成 → web 发版(SPA 静态资源)。回滚:整体 revert,无 schema 变更、
无不可逆数据操作(删除仅用户主动触发)。契约漂移门禁(CI diff)保证
openapi.json 与代码同步提交。

## Open Questions

- 侧栏会话时间分组(今天/近 7 天/更早):纯呈现增强,随迭代定。
- 管理面占位页信息密度(仅一句话 vs 功能预告):下一棒 CRUD 落地时定。
