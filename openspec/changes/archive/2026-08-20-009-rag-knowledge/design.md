# 009 设计 — RAG 三路召回 + 知识库

## Context

- 005 问答链路:retrieve 节点经 `/internal/meta/schema` 实时内省(50 表截断)
  拼 schema 提示;008 已把内省/执行/选项面按 datasource_key 动态化。
- **rag 两表为 baseline 既有资产**(0001 整合上游最终态一次建全:rag schema、
  四元唯一键、vector(1536)+tsv、HNSW+GIN、source_type CHECK;评审 B1 实证)。
  本变更零建表,只做增量演进。
- 信任边界(ADR-002):api 持全部凭证与 PG;agent 零凭证、持 LLM key。
  api↔agent 既有双向通道:api→agent `/query`(问答)、agent→api
  `/internal/*`(执行/内省),均内部令牌。
- 上游 `/Users/solvela/nl2sql-data-platform` 已验证参数(探索报告结论):
  1536 维 + HNSW(vector_cosine_ops)+ GIN(tsv)、tsv 应用层维护('simple'
  配置)、topK=5、RRF k=60 仅融表说明两路、query_history 独立强 few-shot、
  emb 缓存 24h/ctx 缓存 10min(SCAN 精确失效)、成功即回写(无去重上限)、
  **embedding 端点独立配置不随 LLM 主模型路由**(上游 028 审查教训,必须继承)。
- 库与 CI 均为 pgvector/pg17 镜像(已核),扩展已就位。

## Goals / Non-Goals

**Goals:**
- 知识条目成为 schema 提示第一来源,检索收窄到相关表;新租户/空知识零回归
  (内省兜底)。
- 全链路与上游同构的召回质量参数;两处本仓化差异(幂等键含租户维度对齐召回
  过滤、空知识回退内省)已声明且立约。
- 安全规则先行:双过滤隔离/越租户删除/权限点/上传校验/rag 拒读防线全部测试先行。

**Non-Goals(design 级)**:
- 不引入 pgvector 的 SQLAlchemy 方言包(vector 列经绑定参数 + `CAST(:v AS
  vector)` 强转,原生 SQL 即可,api 零新增 pgvector 相关依赖)。
- 不做异步任务化:fetch-metadata 同步端点(见 Risks 批量时长)。
- 不动 SQL_GEN_SYSTEM 等既有 prompt 资产(历史 SQL 段拼在 user 模板)。

## Decisions

### D1 数据模型增量(迁移 0006,ALTER 型)

baseline 已建 `rag.table_descriptions`(四元唯一键 `uk(tenant_id,
datasource_key, schema_name, table_name)`、HNSW embedding、GIN tsv、
source_type CHECK('MARKDOWN','AI_GENERATED','MANUAL_EDIT','FEEDBACK_REFINED')、
DEFAULT 'MARKDOWN')与 `rag.query_history`(HNSW embedding)。0006 增量:
①source_type CHECK 枚举替换为 ('INTROSPECTION','AI_GENERATED','MANUAL_UPLOAD',
'MANUAL_EDIT','FEEDBACK_REFINED') + DEFAULT 改 'INTROSPECTION'——对齐
knowledge spec 五级优先级与上游 introspection/introspection_llm/markdown/manual
四态映射(空表瞬时,先例 0005;downgrade 还原);
②辅助索引 `idx_td_tenant_ds(tenant_id, datasource_key)` 与
`idx_qh_tenant_ds_status(tenant_id, datasource_key, status)`(召回双过滤路径,
baseline 未建);③downgrade 仅回滚自身增量(DROP 索引 + 还原 CHECK/DEFAULT,
不触碰 schema/表——baseline 资产不可删)。**手写迁移**(autogenerate 不识别
vector/hnsw 类型)。四元唯一键是 baseline 既定形态:tenant_id 进键与召回过滤
维度对齐(防御纵深;datasource_key 本仓全局唯一,论据澄清于评审 S1)。

### D2 embedding 调用方向与独立配置(api→agent)

上游同构:api 不持 LLM key,经 `POST {agent}/internal/embedding`(body
`{"texts": [...]}` 批量,上限 64 条;内部令牌,agent middleware 已全局覆盖)
获取向量。agent 侧 `llm.py` 加 `embed(texts)`(openai SDK embeddings API);
**settings 新增独立 `embedding_base_url`/`embedding_api_key`/`embedding_model`
(默认 text-embedding-v2,1536 维),缺省回退 llm_* 组**——上游 028 教训:
LLM 主模型切非通义供应商时 embeddings API 不存在,RAG 会静默全量失败,独立
配置让 embedding 固定走 dashscope。api 侧 `ports.py` 定 `EmbeddingPort`
Protocol(宪法 Article II),实现类 httpx 回调 + Redis emb 缓存
(`km:emb:v1:{sha256}`,TTL 24h,fail-open)。换 embedding 模型必须 bump 版本
前缀 v2 并评估维度迁移(挂账,类比 008 密钥轮换)。

### D3 三路召回与 RRF(上游同构参数)

`/internal/rag/retrieve` 流程:租户解析+datasource 校验(与 meta.py 逐字同构:
default 走本库、非 default 存在+启用+归属同形 400)→ ctx 缓存查
(`km:ragctx:{tenant}:{ds}:{sha256(question)}` TTL 10min)→ miss 时取问句
向量(用 understand 产出的 resolved_question,多轮指代消解后语义——评审 R3)
→ 三路 SQL(**全部限定 `rag.` 前缀 + tenant/ds 双过滤 WHERE + 绑定参数**;
向量一律 `CAST(:v AS vector)` 参数化,禁 f-string 拼接序列化向量——纪律红线):
①向量 `WHERE tenant_id=:t AND datasource_key=:d AND embedding IS NOT NULL
ORDER BY embedding <=> CAST(:v AS vector) LIMIT 5`;
②全文 `WHERE … AND tsv @@ plainto_tsquery('simple', :q) ORDER BY ts_rank(tsv,
plainto_tsquery('simple', :q)) DESC LIMIT 5`;
③历史 `WHERE tenant_id=:t AND datasource_key=:d AND status='SUCCESS' AND
embedding IS NOT NULL ORDER BY embedding <=> CAST(:v AS vector) LIMIT 5`。
①② RRF 融合:`score = Σ 1/(60+rank)` 取 top5;③不进融合,取 sql_text 拼接
「历史成功 SQL」段(最多 3 条,防 token 膨胀)。schema 提示构造(纯函数):
逐表 `schema.table(col1(类型 中文说明),…) -- 表说明` + 表内嵌示例 SQL 段。
**中文全文路局限(R2-S3 声明)**:'simple' 配置不切分中文——tsv token 与
问句 token 须整段一致才命中;中文场景全文路实际仅精确名/英文词命中
(上游同构局限),「embedding 不可用降级全文路」对中文输入近乎恒空。
中文分词(zhparser/自研切分入 tsv)挂账,本期行为=向量路为主、全文路为
英文/精确名修正。
embedding 失败 → ①③空 ②照常(降级不断路);三路全空 → 空结果(由 agent
侧回退内省,见 D4)。非空结果写缓存后返回;**空结果不写缓存**(冒烟实证:
降级空结果入缓存会让恢复后的服务在 TTL 窗口内持续返回空,空结果无缓存价值,
下次请求重试全链路)。

### D4 零命中回退内省(与上游的关键差异)

上游检索侧纯查知识库(空知识=空提示)。本仓保留 005 内省为兜底:retrieve
节点先调召回;**表说明零命中或召回 HTTP 失败 → 回退 fetch_schema 实时内省**
(现逻辑整体保留为 fallback 分支)。理由:本仓租户/数据源动态注册(008),
新接入数据源在管理员跑 fetch-metadata 前知识库必空,空提示=质量断崖;
nl2sql-agent spec delta 已按此立约。

### D5 覆盖优先级(应用层判定)

upsert 前读现有条目 source_type:自动来源(fetch-metadata 的
AI_GENERATED/INTROSPECTION)遇更高优先级既有条目(MANUAL_UPLOAD/MANUAL_EDIT/
FEEDBACK_REFINED)→ 跳过该表计入 skipped;上传(MANUAL_UPLOAD)与 reindex
(先清空)不受限。不用 SQL CASE 表达(太隐晦,审查面差;fetch 批量一次读
优先,往返可接受)。

### D6 缓存与失效

两层 Redis(Redis 8 已有客户端封装):emb(v1 版本前缀,确定性映射,无主动
失效);ctx(TTL 10min)。失效点:上传/删除/fetch-metadata/reindex/datasource
删除;**回写不失效**(design 与上游同构取舍:同问句 history_sql 在 TTL 内
有界滞后,10min 收敛)。实现 `scan_iter(match=…, count=100)`(禁 KEYS)收集后批量 DEL;
**业务事务提交后失效**(service 层 commit 后显式调用——上游 afterCommit 同款,
防提交前并发读回填旧值)。fail-open:Redis 异常按未命中/失效失败仅告警。
datasource 删除后知识条目暂留(Open Questions),但 ctx 失效挂上。

### D7 文档解析(库选型与资源边界)

api 侧新增 `pypdf`(纯 Python,BSD)+ `python-docx`(MIT,仅 .docx)。
扩展名分流:md/markdown/txt→文本直读;pdf→pypdf 逐页 extract_text;docx→
python-docx 段落拼接。**解析为同步 CPU 密集操作,端点内经 `asyncio.to_thread`
执行**(防事件循环卡死,评审 S6);解析失败/空文本→400;上限文本类 1MB、
pdf/docx 10MB(settings 可配);**切块产出表块数上限 500,超限 400**(10MB
文本型 PDF 可切数千块,防 embedding 回调风暴)。备选 pdfplumber(表格抽取
强但重、依赖链长)——表说明文档是文本型,不需要,弃。

### D8 Markdown 切块(上游规则移植)

标题正则 `^##\s+(?:([a-zA-Z_]\w*)\.)?([a-zA-Z_]\w*)\s*$`(MULTILINE,支持
`## schema.table` 与 `## table`);**表名/标题 schema 小写折叠**(PG 未加引号
标识符语义,`## Customer` 与 `## customer` 同表,评审 S3)——上传表单的
schema_name MUST 过 `^[a-z_][a-z0-9_]{0,62}$` 标识符校验(datasource 域
PG_IDENTIFIER_PATTERN 同构复用)。块内按小节边界键(说明/字段/列/columns/
示例/sample,小写匹配)提取 table_comment/columns_comment/sample_queries;
零表块→400。columns 解析:`[,，]` 切项,首空格前=列名(清洗 `[a-zA-Z0-9_]`),
兜底 `{"_raw": 整段}`;示例按分号切。embedding 文本=「表名+表说明+字段说明
原文」(上传/抓取/润色三处同一公式,单一实现点)。纯函数模块,测试先行。

### D9 fetch-metadata 流程

`POST /system/rag/fetch-metadata`(租户上下文+manage 权限+审计
rag.fetch_metadata)→ datasource 校验(存在+启用+归属,同形 400)→ **全量
内省:与 meta.py `_introspect` 同口径**——default 路径 schema 集合
`[tenant_<key>, public]`、动态源 `[default_schema]`,BASE TABLE+VIEW,保留表
排除(`rag.` 两表名不入知识;public 排除后实际仅租户业务表)——但**无 50 表
截断**(独立函数,评审 S5:同口径可让 fetch 落库后提示行为平滑切换)→ 逐表调
agent `/internal/describe-table`(并发上限 4,单表超时 60s)→ 润色结果防御
清洗(真实列过滤/SELECT-only/无 INTO/无中间分号/≤500 字符/≤3 条/文本截断
200)→ 按 D5 优先级 upsert(emb 批量生成)→ ctx 失效 → 返回
{found, enriched, skipped}。
**reindex**:校验同上 → 删该 tenant+ds 全部条目 → 走 fetch-metadata 全流程
→ 返回 {deleted, rebuilt, enriched}。幂等(清空+确定性重建)。

### D10 agent 润色端点(describe-table)

新文件 `kongming_agent/describer.py`:system prompt 上游逐字移植(「数据库
文档专家…只输出 JSON」纪律:新资产入 prompts 体系,文件头标注移植来源);
user 模板=表名/原注释/列清单(`- name (type) -- comment`);输出 JSON
{table_comment, columns_comment, sample_queries};LLM 失败/解析失败→降级
纯结构(列说明=英文类型,示例=[])。端点挂 `/internal/describe-table`
(middleware 已覆盖鉴权)。

### D11 成功 SQL 回写点与问句口径

chat/service.py:查询链路**最终判定 SUCCESS(PASS)后**、消息落库 commit 后:
调 rag service `writeback(tenant, ds, question, sql_text)`——**question=用户
原始问句**(与审计口径一致;召回侧才用 resolved_question,评审 R3);emb
缓存查/取问句向量,INSERT rag.query_history(status='SUCCESS');失败仅告警
不阻断(独立 try,不参与业务事务;不依赖行数——空结果的成功查询也回写,
评审 R4;persist=False 为测试通道不触发)。失败/REJECTED 不回写(spec 立约)。
无去重无上限(上游取舍:相似问句靠余弦自然聚类,挂账 eval 域评估)。

### D12 retrieve/generate 节点改造

apiclient 加 `rag_retrieve(tenant_key, datasource_key, resolved_question) ->
RagContext | None`(None=HTTP 失败);retrieve 节点:召回非空→拼提示+存
history_sql 到 state;空/None→回退现 fetch_schema 分支。generate:state 有
history_sql 且非空→user 模板表结构段后追加「参考历史成功 SQL:」段(系统
模板资产零改动)。AgentState 加 `history_sql: str`。

### D13 契约与前端

api 新端点 6 个(/system/rag/* 5 + /internal/rag/retrieve)后 `make contracts`
重生成;web `lib/rag-api.ts` 封装(类型全生成);`/admin/rag` 页(shadcn
table/dialog/alert-dialog 既有组件复用,上传用隐藏 file input 管道元素豁免
+ Button 组合);AdminLayout 子导航加「知识库」(system:rag:manage)。
列表接口/manage(评审 S2:无用户面消费方,与页面守卫、同域写操作对齐);
租户头复用现有 X-Tenant-Id 注入。

### D14 执行面 rag 拒读防线(评审 B2,测试先行)

rag 两表灌入真实问句/SQL/表结构后,`SELECT … FROM rag.query_history`(显式
限定名,search_path 管不了)可跨租户读取——现有保留表判定(前缀 sys_/chat_+
精确名)不含 rag,实证缺口。修:`sqlfw` 保留表判定扩容——表引用的显式
schema 限定名(sqlglot Table.db,小写比对)为 `rag` 即拒。**两表末段名不进
RESERVED_TABLE_NAMES**:rag 不在任何执行路径 search_path 内,裸名落 public
解析到不存在的表(执行错误,无泄漏通路);且 query_history 是常见业务表名,
末段名保留会误伤真实业务库。CTE 遮蔽形态(体内限定名)由表扫描天然覆盖。
meta 内省排除同判定(default/动态源 schema 集不含 rag,天然不出现,无需改)。
sql-execution spec delta 立约。km_readonly 对 rag 的 GRANT(baseline)不
回收——与 sys_ 表同构,防火墙为防线(005 结论:执行用 database_url owner,
授权链路非 live 路径)。

## Risks / Trade-offs

- [fetch-metadata 同步端点在大库下时长不可控(每表一次 LLM,3-5s/表)] →
  上游同款同步形态;sakila 16 表/olist 8 表实测 <2min 可接受;httpx 客户端
  超时 300s 兜底;异步任务化挂 Open Questions(不改契约,只加异步执行形态)。
- [HNSW 索引在空表/少量行时召回质量退化(pgvector 已知特性)] → 知识库冷启动
  阶段命中数少,由 D4 内省兜底吸收;表数增长后自然收敛。
- [ctx 缓存失效窗口:evict 后并发读回填旧值] → 上游同款;TTL 10min 有界,
  写操作均为管理员低频操作,可接受。
- [emb 缓存与维度/模型强耦合] → key 带版本前缀 v1;换模型 bump v2+评估维度
  迁移(新迁移),挂账(类比 008 密钥轮换挂账)。
- [query_history 无限增长] → 上游取舍沿用;召回有 status+双过滤索引,查询
  面可控;TTL 清理/去重挂 eval 域。
- [agent embedding 端点成为新内部攻击面(内部令牌内网)] → 与既有 /query
  同防线;仅出向量不出数据;embedding 调用计日志不记原文(审计纪律)。
- [润色 prompt 为新资产,手抄丢全角标点风险] → describer.py 文件头注释标注
  移植来源;必要时补码点对拍(005 经验)。
- [上传/解析的资源面] → D7 三重界:大小上限(1MB/10MB)+ 表块数上限 500 +
  to_thread;manage 权限者才可触达。

## Migration Plan

1. 迁移 0006(CHECK 枚举替换+辅助索引;空表瞬时,downgrade 还原自身增量)。
2. 种子权限 system:rag:manage(排序 5;SUPER_ADMIN 自动覆盖,DEVELOPER 不推
   ——管理面权限,与 datasource:manage 同待遇)。
3. sqlfw 拒读扩容先于任何知识灌入(测试先行,顺序见 tasks 1.4)。
4. 契约重生成+web 类型消费;`.env.example` 补 KM_EMBEDDING_* 注记。
5. 回滚:迁移 downgrade + 端点下线即可;rag 表无外部引用,零数据耦合。

## Open Questions

- datasource 删除(008)后残留知识条目的级联清理:本期不级联(datasource
  删除低频+知识重建成本高;残留条目不可被召回命中——需 datasource 校验通过
  才进召回,天然 fail-closed)。级联删除 vs 保留策略随 multitenant 管理变更
  再定,不阻塞本变更。
- fetch-metadata 异步任务化(大库 >100 表场景):本期同步+挂账;异步化只改
  执行形态不改契约。
- describe-table prompt 码点对拍脚本:实施期视手滑风险决定是否补 005 式
  校验脚本(资产仅 1 个模板,风险面小于 005 的批量移植)。
