# 039 chat-suggested-questions — Tasks

纪律:租户隔离/可用性同形收敛属安全规则类,**测试先行**(先写失败测试再实现);
每个任务组完成后跑 make check 对应子集并在本文件勾选记录结果。

## 1. 迁移与存储(api)

- [x] 1.1 迁移 0027:新表 `public.chat_suggestion`(UUID v7 主键、tenant_id、
  datasource_key VARCHAR(64)、questions JSONB、created_at/updated_at
  timestamptz、UNIQUE(tenant_id, datasource_key)),up/down 双向,链式依赖 0026
- [x] 1.2 chat 域 models 增 Suggestion 领域模型 + repository(读/写/按源删,
  ON CONFLICT 幂等落库;事务收口对齐 chat 既有 repo 惯例)

## 2. chat 域服务(测试先行)

- [x] 2.1 失败测试先行:租户隔离(跨租户数据源 key 与不存在同形空清单)、
  可用性预检(禁用/非法 key 空清单不触发生成)、缓存命中不生成、未命中生成后
  落库、生成失败空清单不落缓存、产出防线校验(超限条目丢弃/全畸形降级)
  ——测试红
- [x] 2.2 chat 域 ports 增 `SchemaOverviewSource` 与 `Suggestor`(agent 回调)
  窄端口;service 实现 suggestions 用例(预检→摘要→缓存查→advisory lock
  取锁复查→生成→防线校验→落库→返回;任一环失败收敛空清单)
- [x] 2.3 组合根适配器:internal/meta 同源内省组装摘要(保留表过滤/注释/
  单 schema/表数 ≤30/字符 ≤12k 截断);chat repository 实现 SuggestionCacheEvictor
  语义所需查询;2.1 测试转绿
- [x] 2.4 并发防击穿测试:同租户同源并发首访仅一次生成、落库幂等
  (pg_advisory_xact_lock + 取锁后复查);make check api 子集绿

## 3. datasource 失效级联(api)

- [x] 3.1 datasource/ports.py 增 `SuggestionCacheEvictor` 窄端口;service 在
  update(连接信息任一变更,仅改 name 不触发)与 delete 的管理事务内调用
  (与 SemanticCleaner/RagCtxEvictor 同装配点同事务语义)
- [x] 3.2 失败测试先行→实现:编辑连接信息/删除后旧建议不再返回、仅改 name
  不失效;组合根注入 chat 侧实现;make check api 子集绿

## 4. agent 生成面

- [x] 4.1 新模块 `kongming_agent/suggester.py`:SYSTEM prompt 全新撰写
  (全角纪律,输出 JSON `{"questions": [...]}`,仅结构内可答/统计明细兼有/
  3-5 条/每条 ≤60 字);产出清洗单源(剥围栏/逐条 strip+单行折叠+封顶+
  去重+上限 5);temperature 0.3、max_tokens 400、task="SUGGEST"
- [x] 4.2 单测:prompt 全角断言 + 清洗边界(超长/多行/重复/全畸形)+ 降级
  契约(解析失败/LlmError → generated=False 不抛 5xx,与 rewrite-title 同构)
- [x] 4.3 端点 `POST /internal/suggest-questions`(请求/响应模型精确,
  X-Internal-Token 中间件既有覆盖);make check agent 子集绿

## 5. 端点与契约(api)

- [x] 5.1 chat router 增 `GET /chat/suggestions?datasource_key=`,
  response_model 精确挂载(`{items: string[]}`),鉴权 current_user +
  X-Tenant-Id 复校(越权 403 TENANT_FORBIDDEN 同既有面);api 侧 Suggestor
  适配器调 agent 新端点(AgentUnavailable → 空清单)
- [x] 5.2 端点级测试:未登录 401、越租户 403、default 缺省语义、响应形态
  与缓存副作用;`make contracts` 重生成,openapi.json + 前端类型入库;
  make check api 子集绿

## 6. 前端(web)

- [x] 6.1 chat-api 增 getSuggestions(生成类型,零手写);ChatPage 增
  useQuery(["chat-suggestions", current, datasource],新建态干净分支 enabled)
- [x] 6.2 EmptyState 品牌区下方建议条目:加载 Skeleton 三条、空/失败静默
  null、点击 `submit(q)` 直发(submit 重构为 `submit(text = draft.trim())`,
  与手输同链路同守卫);条目为 shadcn 组件形态(禁裸 button),样式走
  @theme token,lucide 图标
- [x] 6.3 浏览器实测(dev 起栈,admin 登录):空态呈现 chips→骨架→生成落库、
  点击即问全链路流式、切数据源/租户换档、失败源静默隐藏、提问进行中不呈现;
  tsc + eslint 绿
  (完成记录:Playwright 实测 admin@dev 栈——空态 5 chips 渲染;点击首条即问,
  自动建会话导航,流式→终态卡片全要素接管;返回新建态 chips 缓存复现;
  切 sakila_dyn 即时换档不残留;截图 039-click-to-ask-result.png;tsc/eslint 绿)(dev 起栈,admin 登录):空态呈现 chips→骨架→生成落库、
  点击即问全链路流式、切数据源/租户换档、失败源静默隐藏、提问进行中不呈现;
  tsc + eslint 绿

## 7. 收尾

- [x] 7.1 全量 `make check` 绿(api/agent ruff+mypy+pytest、web tsc+eslint,
  后台跑须核对 make-exit 行);契约零漂移确认
- [x] 7.2 docs 搭车:HANDOFF 当前状态一句话同步(变更明细归 git log,不进
  HANDOFF);评估/演进史如有涉及处同步一句
