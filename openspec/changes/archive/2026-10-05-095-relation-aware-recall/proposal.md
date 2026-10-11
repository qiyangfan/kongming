# 095-relation-aware-recall 提案:关系感知的召回扩展(默认开启)

## Why

触发点是一篇行业文章(Ontology vs Vector Embeddings,2026-04-17):其内核判断是
「对结构化业务数据提问,显式关系比向量相似度更决定成败」,方案是向量 + 图遍历 +
本体推理混合。该文的论证方式不可信(20 条查询的对照、"研究显示 40%"、Palantir
因果归因、以及"向量 RAG 将死"的伪二分——它自己给的架构就是混合),但**机制层
有一点对本仓是新缺口:按已声明关系反向扩展召回集**。其余(图数据库/RDF/OWL/
"ontology 取代向量")本仓明确不做,见 Non-goals。

现状缺口(代码核验,非推测):

1. 召回集完全由三路相似度决定:`rag/service.py:recall` 三路 → 闸门 → RRF →
   `fetch_entries`;表关系在召回**之后**才被消费——`rag/router.py` 的
   `/internal/retrieve` 里 `relations_hint` 只描述「已召回表集合内」的两两关系。
2. 于是一类失败是确定性的:**问句命中实体 A,答案度量落在 A 的关联表 B,而 B 与
   问句的词面/语义都不接近** → B 进不了召回集,LLM 只能拿 A 硬凑(单表近似、
   猜 JOIN 列),或被 051 哨兵误判为"无数据"。
3. 关系数据早就躺在库里(`rag.table_relations`,introspection + 手工两类),
   只是**没被用来找表**,只被用来描述已找到的表之间的路。

方案:召回主结果确定后,按既有声明关系做**一跳扩展**,把直接关联表补入召回集
(尾附加),并让 relations_hint 的收窄基准随之扩展(不带 JOIN path 的扩展表对生成
无增益,只是稀释)。

### 为什么默认开启、不做灰度

064 的 off/shadow/primary 三态是**为生产流量的阈值标定**服务的:阈值缺省值
未知,必须先采分布再定值,灰度是为了不误伤真实用户。本变更不适用该前提:

- 判据是二值的——「关系行在不在」没有需要标定的中间量,分布采集没有消费方;
- 本项目无生产流量、无既定基线(评估集分数是唯一质量信号),影子阶段只会让
  收益证据晚一个迭代才出现;
- 收益与风险都可即时观测:试跑端点(064 已建)可直接看扩展补了什么,评估集
  (096)可立即量化对 Execution Match 的影响。

因此本变更为**缺省开启 + 整项 kill switch**(关停即现状,逐字一致),不做影子态。

## What Changes

1. **rag 域新增关系扩展端口**(Protocol,宪法 Article II):`related_tables(tenant_id,
   datasource_key, tables) -> [{source_table,target_table,join_keys}]`,实现为
   semantic 仓储的新查询(任一端在表集合内即命中);扩展判定为**纯函数**(入参=
   召回键 + 关系行 + 上限),无 IO,可单测。
2. **recall 主链路在 RRF 融合与 `fetch_entries` 之间插入扩展**:一跳、去重、
   上限(可配,缺省 2)、尾附加;扩展条目与主召回条目同形态
   (`schema.table(alias)(列…)`),SHALL NOT 新增段落类型或标记(Agent 侧解析与
   080 预算截断逻辑零改;预算超限时按既有「尾部丢弃」方向先裁扩展,主召回不受影响)。
3. **relations_hint 收窄基准随之扩展**:`/internal/retrieve` 从 schema_hint 提取
   表集合的既有逻辑自动覆盖扩展表(同形态),无需改 agent;spec 侧在 semantic-layer
   域把「召回表集合」定义澄清为含扩展结果(只改定义,管理端点行为零变化)。
4. **缺省开启 + kill switch**:`rag_expand_enabled`(缺省 True)、
   `rag_expand_max`(缺省 2)。关停时输出与引入前逐字一致(零回归铁律)。
5. **缓存失效补漏(正确性,非优化)**:召回输出自此依赖关系数据,故关系写路径
   (introspection upsert / 手工关系增删 / 数据源删除级联清理)SHALL 在提交后
   失效该租户+数据源的全部检索上下文缓存(`ContextCache.evict` 既有能力)。
   否则新增关系在 TTL 600s 窗口内不生效——静默失效,用户感知为「配了却没变化」。
6. **试跑端点透出**:`GET /system/rag/recall-probe` 返回值增扩展明细(补入表、
   经哪条关系、扩展后表集合),零副作用纪律不变。知识运营上传表说明后可直接
   验证「问 X 能否连带召回 Y」。
7. **观测**:本轮扩展的表与经由关系进 trace(SHALL NOT 静默)。

## Non-goals

- **图数据库 / RDF / OWL / Neo4j**——不做,也不预研。ADR-001 否决清单 +
  tech-stack 锁栈:一跳遍历的收益已被 `table_relations` 的 JOIN path 表达,
  为一个遍历新引重型存储不成立。若未来确需约束推理与多跳,先写 ADR。
- **多跳扩展**——不做。一跳可解释、可测、可回滚;多跳在几十张表的库上即组合
  爆炸,且把「关系」变成新的稀释源(与 064 闸门要解决的问题同向)。
- **LLM 从文本抽取关系补全本体**——不做。抽取结果置信度不可验证,而错 JOIN
  比无 JOIN 更贵(静默错关系是最坏形态)。
- **关系置信度 / 权重排序**——不做,所有声明关系同权(introspection 与 manual
  亦同权,与现有 relations_hint 口径一致)。
- **扩展表自动补 JOIN 或执行模板**——不做,扩展只进提示词,SQL 仍由 LLM 生成
  并走防火墙 + 只读池全量防御(023 铁律不变)。
- **影子 / 灰度机制**——不做,见 Why 末节:二值语义无标定对象,采集无消费方。
- **per-datasource 扩展管理台**——不做,先全局可配 + 试跑端点验证。
- **指标可见性按 data_scope 过滤**——不做,另立 backlog 待办(远期为跨部门
  共享知识库的治理项,见 docs/roadmap.md)。

## Capabilities

### Modified Capabilities

- rag:新增「关系感知召回扩展」Requirement——一跳扩展 / 只走已声明关系 /
  去重与上限 / 尾附加 / 缺省开启可整项关停 / 关系写后缓存失效 /
  试跑与 trace 透出。
- semantic-layer:「生成链路注入」Requirement 定义澄清——relations_hint 的收窄
  基准「召回表集合」含 095 关系扩展结果(管理端点行为零变化)。

## Impact

- **落点**:`apps/api/kongming/rag/ports.py`(新 Protocol)、`rag/models.py`
  (扩展纯函数)、`rag/service.py`(recall 插入扩展)、`rag/router.py`(组合根注入
  适配器;试跑端点透出)、`semantic/repository.py`(`relations_touching` 查询)、
  `semantic/router.py`(写路径后 evict ctx 缓存,经窄端口)、`config.py`(两个
  新配置项)。
- **无 schema 变更**(ADR-004):复用既有 `rag.table_relations`,不建表不加列。
- **无 OpenAPI 信封变更**(ADR-005 零触发):扩展只改 schema_hint 内容,不改
  响应结构;试跑响应为既有 `dict[str, object]` 信封,内容透出不改 schema 形态
  ——`make contracts` 跑一遍确认零漂移即可。
- **信任边界无涉**(ADR-002):全部在控制面召回路径内,agent 面零感知。
- **依赖**:与 096 同期落地;096 的失败归因是本次扩展的收益证据来源
  (SELECT/JOIN 类计数变化 + Execution Match 对照),两变更任一齐备即可互相读数。
