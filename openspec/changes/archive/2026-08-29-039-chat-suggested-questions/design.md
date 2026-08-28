# 039 chat-suggested-questions — Design

## Context

- 主对话空态(`ChatPage` 的 EmptyState)零可交互元素;商店 bot 已有
  greeting+suggestions(创建者手填、点击即问),主对话无对应物,且面对任意
  schema 不可手填(见 proposal Why)。
- 既有通路全部现成:api→agent 内部回调五先例(embedding/describe-table/
  rewrite-title/extract-memory/models,X-Internal-Token,降级契约=失败不
  5xx、api 侧收敛);schema 内省单源在 `internal/meta`(保留表过滤+注释+
  truncated);chat 域跨域惯例=ports 反转(DatasourceChecker/QueryHistoryWriter);
  datasource 域级联清理窄端口先例=SemanticCleaner(023)/RagCtxEvictor(009);
  LLM 任务路由与计量=021/038(`llm.chat(task=...)` 缺省回退主模型,
  fire-and-forget 落 sys_llm_usage,失败也采集)。
- 迁移序列至 0026,本变更 0027。

## Goals / Non-Goals

**Goals:**

- 新用户在空态即可一键发起「该数据源真实可答」的提问;零人工、新租户接入即用。
- 全链复用既有范式,不引入新依赖、新权限点、新基础设施。

**Non-Goals:**

- 见 proposal Non-goals(热门聚合/人工覆写/非空态展示/收编 bot suggestions/
  rag 重同步主动失效/换一批与多语言)。

## Decisions

### D1 域归属:chat 域拥有端点+缓存,datasource/rag 只出窄端口

端点 `GET /chat/suggestions`、缓存表、生成编排全落 chat 域。理由:消费面是
对话入口(auth、X-Tenant-Id 复校、`_resolve_tenant`、agent_client 通道都在
chat);能力跟着消费面走(store bot suggestions 落 store 同理)。备选
datasource 域——否决:生命周期贴身但用户面鉴权/租户解析得跨域借道,且其
既有用户面端点(/options)是管理面例外而非对话面。

### D2 schema 摘要:api 侧复用 internal/meta 同源内省,经窄端口注入

chat 域定义 `SchemaOverviewSource`(Protocol:`overview(tenant_key,
datasource_key) -> str | None`),适配器在 api 侧复用 meta 内省语义(保留表
过滤防凭证探测、obj_description 注释、default_schema 单 schema、truncated),
组合根注入;域层零框架依赖。摘要封顶:表数 ≤30、总字符 ≤12k,超出截断注记。
备选 A:读 rag 知识块(富化注释/别名)——否决:rag 块可滞后/缺失,内省才是
单一真理源;备选 B:agent 持回调反拉 /internal/meta——否决:api→agent→api
环形回调,describe-table 先例均为「api 持元数据、agent 纯脑力」。

摘要不可得(数据源不可用/内省失败)→ 返回 None → service 直接收敛空清单,
不触发生成(与可用性预检合并为一道闸)。

### D3 agent 侧:/internal/suggest-questions + suggester.py(与 titler 同构)

新模块 `kongming_agent/suggester.py`:SYSTEM prompt 全新撰写(非上游资产,
全角纪律,输出 JSON `{"questions": [...]}`),输入=摘要文本,要求「仅依结构
可答的中文问句,统计与明细兼有,3-5 条,每条 ≤60 字」;temperature 0.3,
max_tokens 400,`task="SUGGEST"`。端点降级契约与 rewrite-title 完全同构:
任何失败(解析/校验/LlmError)返回 `generated=false`,不抛 5xx。

产出清洗单源放 agent 侧(同 describer 清洗列名职责):剥围栏/取 JSON、逐条
strip+单行折叠+≤60 字、去重、上限 5 条;api 侧落库前再做一道防线校验
(非空+条数 ≤5+单条 ≤100 字)——纵深一层,代码量可忽略。

计量:021 注册表不配 SUGGEST 即回退主模型(告警去重);需要省成本时 env 配
`SUGGEST=小模型` 即定向路由,零代码。

### D4 缓存表:public.chat_suggestion(迁移 0027)

```
id UUID v7 PK
tenant_id       UUID NOT NULL
datasource_key  VARCHAR(64) NOT NULL
questions       JSONB NOT NULL          -- string[]
created_at / updated_at timestamptz NOT NULL DEFAULT now()
UNIQUE (tenant_id, datasource_key)
```

UNIQUE 即查询路径索引,不另建。不设 FK 到 sys_datasource:default 内置源无行
可挂,删除失效走 D6 端口级联(与 023 语义层同款,不强依赖物理外键)。
备选:sys_datasource 加 JSONB 列——否决:管理面聚合混入对话面缓存,default
无行。

### D5 并发防击穿:事务内 advisory lock + 取锁后复查

`pg_advisory_xact_lock(hashtext(tenant_id || ':' || datasource_key))`,
区间=取锁→复查缓存→调 agent→落库→提交;后到者取锁后复查命中即直接返回,
不重复生成。持锁含一次 LLM 调用(数秒),锁粒度=单租户单源,别源零扰动;
同行竞争者本就在等同一份结果,等待可接受。备选 Redis SETNX——否决:跨存储
一致性平添复杂度,单库事务锁最简,与 022 wCTE 竞态 advisory lock 同尺;
备选放任竞态 ON CONFLICT 去重——否决:多余 LLM 调用虽小,锁代价更低且符合
竞态纪律。

### D6 失效:datasource 域窄端口 SuggestionCacheEvictor,同事务级联

`datasource/ports.py` 增 `SuggestionCacheEvictor`(`evict_by_datasource(
datasource_key)`,跨租户全删),chat 域实现,组合根注入 datasource service
(与 SemanticCleaner/RagCtxEvictor 同一装配点)。触发点:update 连接信息
(host/port/database/username/password/default_schema)任一变更、delete——
与既有池失效同一判据;仅改 name 不触发。缓存行删除与管理变更同事务(失败
即回滚,一致性最强)。disable/enable 不失效:结构未变,恢复启用后旧建议
仍有效;禁用期由可用性预检挡住不呈现。

### D7 端点与可用性口径

`GET /chat/suggestions?datasource_key=`(query 参数蛇形,对齐 038 timeline);
鉴权=current_user + X-Tenant-Id 复校(复用 chat `_resolve_tenant`,越权 403
TENANT_FORBIDDEN 同既有面);**response_model 精确挂载**(ADR-005 教训:
GET 端点必挂,否则前端手写类型);响应 `{"items": string[]}`。可用性预检
复用 `DatasourceChecker.is_usable`(008 D8)——default 内置直通语义随其
既有实现;不可用/摘要不可得 → 200 `items=[]`(spec「同形收敛」,不区分
原因防 key 探测)。

### D8 前端:空态 chips + submit 提参直发

`ChatPage`:`useQuery(["chat-suggestions", current, datasource])`,
enabled=新建态干净分支(无 sessionId、非 streaming/finalized/askError);
EmptyState 品牌区下方渲染条目(加载=Skeleton 三条;空/失败=null)。点击
调用 `submit(q)`——submit 重构为 `submit(text = draft.trim())`,chips 直发
与手输同链路(streaming 守卫、流式呈现、终态接管全复用);发送后 streaming
置位,空态分支自然退出。切换租户/数据源随 queryKey 换档。类型经
make contracts 生成,chat-api.ts 增 getSuggestions。

## Risks / Trade-offs

- [LLM 生成问句质量差(答不出/太泛)] → 摘要含注释与类型,prompt 约束
  「结构内可答」;点击后走既有问答链路,答不好归 NL2SQL 既有质量域,本域
  不双背。
- [schema 摘要注入(恶意表/列注释指挥 LLM)] → 产出仅为呈现文本+逐条校验
  封顶,不进任何执行路径;信任级别与 describe-table(注释本就出域)一致。
- [首次访问延迟(LLM 数秒)] → 骨架过渡;缓存后零延迟;失败静默自愈。
- [缓存陈旧(库结构演进但连接信息未变)] → 容忍(Non-goal);结构性变更
  实践中伴随重建数据源(连接信息变)即触发失效。
- [点击即发的误触成本] → 与商店 suggestions 点击即问同尺;代价=一次问答,
  可接受。
- [advisory lock 持锁跨 LLM 调用] → 粒度单租户单源、竞争者等的是同一结果;
  若未来生成耗时恶化,再收敛为「锁护标记+异步生成」形态(届时改实现不改
  契约)。

## Migration Plan

迁移 0027 单新表,无回填;down 直接删表。滚动部署:先 agent 后 api 最优;
反序亦无害——api 回调对旧 agent 得 404 → 降级契约收敛空清单,chips 静默
隐藏,功能缺席无错误。回滚:回退代码即可,残留缓存行无消费方(可留)。

## Open Questions

(无——default 在 DatasourceChecker 的直通语义以既有实现为准,实现期对齐,
不影响契约与拆解。)
