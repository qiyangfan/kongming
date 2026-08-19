# 006 tasks — 前端主布局:会话侧栏 + 对话区 + RBAC 菜单守卫

> 纪律:安全规则类(会话隔离/权限/租户)测试先行——先写失败测试再实现;
> 每个 task 收尾跑 make check 对应子集并记录。

## 1. api:会话列表/消息历史/删除端点(测试先行)

- [x] 1.1 失败测试:GET /chat/sessions——本人本租户过滤、updated_at 倒序、
  limit 缺省 50/上限 200、无 chat:execute 403、未认证 401
  (参考 specs/chat delta「会话列表与消息历史端点」全部场景)
- [x] 1.2 失败测试:GET /chat/sessions/{id}/messages——升序回显、
  user/assistant 要素结构、REJECTED 轮文案、越权/跨租户/不存在统一 404
- [x] 1.3 失败测试:DELETE /chat/sessions/{id}——本人删除成功且消息级联
  (库内断言)、重复删除 404、越权 404
- [x] 1.4 实现:repository 增 list_sessions/list_messages/delete_session;
  service 增对应用例方法(复用租户解析与 find_session_owned 归属校验,
  越权统一 SessionNotFound);router 增三端点(require_perm + X-Tenant-Id
  依赖链与 /chat/query 同构);测试转绿
- [x] 1.5 result_json 增量补存:error 分态文案 + chart 完整对象
  (替代裸 chartType,纯加字段);回显读档优先、存量缺失时
  suggest_chart 确定性重算兜底;补测试锁定两条路径
- [x] 1.6 `make contracts` 重导出 openapi.json 并再生成 web 类型;
  `cd apps/api && make check`(ruff/mypy/lint-imports/pytest)全绿

## 2. web:路由骨架与守卫

- [x] 2.1 shadcn 补件:sheet/dropdown-menu/alert-dialog/scroll-area/
  avatar/separator/skeleton(CLI 落地,不手搓)
- [x] 2.2 路由重构:`/` 重定向 `/chat`;`/chat` 新对话态;`/chat/:sessionId`;
  `/admin/*` 占位路由组;403/404 品牌页(返回对话首页出口);
  `<Protected>` 认证/首登改密分流保持
- [x] 2.3 RBAC 守卫:lib/nav.ts 导航配置单源(id/label/icon/to/perm)+
  hasPerm 谓词 + `<PermRoute perm>`(无权限 403,与菜单过滤同源);
  lib/perm-gate 或等价组件供菜单与路由共用
- [x] 2.4 api.ts 统一注入 X-Tenant-Id(localStorage `km.tenant` 单源);
  租户 store:默认取 MeOut.tenants 首项、切换时 invalidate 会话域查询
  并导航 /chat;类型补 SessionItemOut/SessionMessageOut(生成产物)

## 3. web:应用壳与侧栏

- [x] 3.1 AppLayout 壳:侧栏(品牌区/新对话/会话列表区/导航区/用户区)+
  内容区 Outlet;导航区按 nav 配置 + 权限过滤渲染,无可见项不渲染空容器
- [x] 3.2 响应式:md 断点以下侧栏收抽屉(sheet),菜单按钮唤出/遮罩关闭,
  交互与常驻态一致
- [x] 3.3 会话侧栏:useQuery(['sessions', tenant]) 列表(倒序/当前高亮/
  skeleton 加载态);新建=导航 /chat(零请求);删除=alert-dialog 确认 →
  DELETE → invalidate + 当前会话被删时回 /chat
- [x] 3.4 用户区:avatar + dropdown(昵称/角色概览/登出);
  租户切换器(多租户 dropdown/单租户只读)

## 4. web:对话区与图表

- [x] 4.1 消息回显:useQuery(['messages', sessionId, tenant]) 升序渲染;
  user 问句气泡 + assistant 结构化卡片(摘要/表格/SQL 折叠/洞察/统计);
  SUCCESS/REJECTED/FAILED 三态视觉区分(REJECTED/FAILED 附原因文案,
  存量缺失走通用文案)
- [x] 4.2 提问框:常驻底部,Enter 发送/Shift+Enter 换行;mutation 调
  POST /chat/query(session_id 取路由参数,不传 history/persist);
  isPending 防重复;成功后 invalidate sessions+messages(新会话导航到
  /chat/:newId);空会话品牌空态引导
- [x] 4.3 图表:chart.type=line/pie/bar 渲染(none 不渲染);
  色板经 getComputedStyle 从 token(--primary/--ring 等)派生,禁硬编码;
  历史回显同样适用
- [x] 4.4 `cd apps/web && pnpm typecheck && pnpm lint` 全绿

## 5. 端到端冒烟与收尾

- [x] 5.1 本地起 pg/redis/api/agent/web(真 LLM key 已配),Playwright MCP
  实测:登录 → 提问(新会话建立/侧栏出条目)→ 多轮 → 刷新恢复 →
  切换会话 → 删除会话 → 切换租户(sakila/olist 数据域)→
  无权限账号菜单守卫与 403 直达 → 404;记录冒烟路径与截图
- [x] 5.2 make check 全量绿(api/agent/web);审查循环(独立评审 → 修复 →
  复核)后归档:spec deltas sync、HANDOFF 更新
