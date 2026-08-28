# 039 chat-suggested-questions — 对话冷启动建议问题域

## Why

主对话新建态空态只有品牌视觉与一句静态泛指文案,新用户冷启动无从下手:不知道
能问什么、也不知道能力边界在哪(统计还是明细?能否画图?能否追问?)——冷启动
引导是数据问答产品的第一流失点。商店 bot 已有 greeting+suggestions 先例,但
主对话面对任意租户的任意 schema,手填模式不可移植;而 schema 元数据本就在
NL2SQL 链路内,LLM 一次调用即可产出「该数据源真实可答」的示例问句——零人工、
新租户接入即冷启动可用。

## What Changes

- **新端点(api chat 域)**:`GET /chat/suggestions?datasourceKey=`——按
  租户+数据源维度返回建议问题;登录即可(对齐 /chat 会话面),无新权限点。
- **缓存(迁移 0027)**:新表 `chat_suggestion`(UNIQUE(tenant_id,
  datasource_key),questions JSONB)——命中即返;未命中懒生成后落库,永久缓存;
  生成失败不落缓存(下次访问自然重试,自愈)。
- **生成链路(api→agent 内部回调,ADR-002 同构)**:api 侧组装 schema 摘要
  (与 internal/meta 同源内省:保留表过滤/注释/截断语义,限张数封顶),回调
  agent `/internal/suggest-questions`;agent LLM 产出 3-5 条中文自然语言问句
  (task=SUGGEST,021 任务路由缺省回退主模型;021/038 用量计量自动覆盖);
  降级契约与 rewrite-title/describe-table 同构——失败不 5xx,api 侧收敛空清单。
- **产出校验**:逐条非空/单行折叠/长度与条数封顶,畸形条目丢弃,全畸形按生成
  失败降级;建议问题仅为 UI 呈现文本,不构成任何执行放行依据。
- **并发防击穿**:同租户同数据源并发首请求经 advisory lock 收敛,至多一次生成,
  落库幂等(后到者取锁后复查命中)。
- **缓存生命周期**:数据源连接信息变更/删除 → 该源建议缓存失效(datasource 域
  新增窄端口、chat 域实现,与 SemanticCleaner/RagCtxEvictor 同构);禁用/越属
  由既有可用性预检口径覆盖,静默收敛空清单。
- **前端(web)**:ChatPage 新建态空态品牌区下方渲染建议 chips(加载骨架;
  失败/空清单静默隐藏,不阻塞品牌空态本身);点击即发送(与商店 suggestions
  「点击即问」同一把尺子);租户/数据源切换随既有查询键自然换档。
- 搭车:OpenAPI 契约重生成入库(make contracts,ADR-005);docs/HANDOFF 当前
  状态一句话同步。

## Capabilities

### New Capabilities

- `chat-suggestions`:对话冷启动建议问题——按租户+数据源的获取与缓存命中、
  schema 摘要生成链路与降级、产出校验、缓存生命周期(失效/并发防击穿)、
  空态呈现与点击发送、租户隔离与数据源可用性口径。

### Modified Capabilities

(无——web-nav「对话区渲染」既有空态契约(品牌文案+提问框可用)不被破坏,新增
呈现行为由本域自立约;datasource 既有级联清单不动,缓存失效行为同理由本域
自立约。)

## Impact

- **表结构(ADR-004)**:迁移 0027 新表 `public.chat_suggestion`(UUID v7
  主键、timestamptz、questions JSONB、UNIQUE(tenant_id, datasource_key));
  无既有表改列。
- **api**:chat 域(router 新端点、service 用例、ports 新窄端口、repository、
  models);datasource 域 ports 增一个缓存失效窄端口(组合根装配一处);
  internal/meta 的内省以适配器复用(组合根注入,域层零框架依赖);既有端点
  零改动。
- **agent**:新模块 suggester(SYSTEM prompt 全新撰写,全角纪律)+ 端点
  `/internal/suggest-questions`;`llm.chat(task="SUGGEST")` 走 021 路由与
  021/038 计量(缺省回退主模型,注册表零配置即可用);零新依赖。
- **web**:ChatPage 空态 chips 与直发通道(submit 提参重构);类型经
  make contracts 生成(ADR-005),零手写 API 类型。
- **契约**:新增 1 个用户面端点,openapi.json 与前端类型重生成入库。
- **权限**:零新增权限点(登录即可 + 租户白名单复校,与 /chat 会话面同口径)。
- **Non-goals**:
  - 不做热门问题聚合/提问日志挖掘(单租户冷启动无历史、跨租户不可复用、
    有隐私面;有数据积累后随演进另行评估)。
  - 不做管理端人工覆写与手动刷新端点(缓存仅随数据源生命周期失效;覆写
    随真实需求评估)。
  - 不做非空态场景展示(会话中途推荐、追问引导不在本期)。
  - 不收编 bot 商店 greeting/suggestions 机制(创建者策展与 schema 生成
    语义不同源,两套并存)。
  - 不做 rag 知识重同步后的主动失效(容忍建议滞后,重同步非高频操作)。
  - 不做「换一批」与多语言问句(中文为主,UI 单行 chips)。
