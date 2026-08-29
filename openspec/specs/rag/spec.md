# rag 能力域

## Purpose

RAG 检索体系:文档上传 → embedding → pgvector 召回,替代 schema 硬编码提示。
覆盖:文档上传索引、三路混合召回(表说明向量 + 全文 + query_history)、知识库管理、成功 SQL 回写。

受影响权限层级: 接口级(system:rag:manage)+ 租户级(按 tenant 隔离检索)

## Requirements

### Requirement: 可扫描 Schema 枚举

系统 SHALL 提供按数据源枚举可扫描 schema 集合的管理端点
(`GET /system/rag/schemas?datasource_key=`,要求 system:rag:manage 权限 +
租户上下文白名单复校,携带 response_model)。集合与内省扫描同源同口径:
default 数据源 SHALL 返回 `[tenant_<租户key>, public]`;动态数据源 SHALL
返回 `[default_schema]`;datasource_key 不存在/禁用/不归属当前租户 SHALL
fail-closed 拒绝(同形,防 key 探测)。该集合为知识上传 schema 字段的唯一
候选来源,SHALL NOT 出现第三处独立计算(单一真理源)。

#### Scenario: default 数据源枚举

- GIVEN 管理员已登录且持 system:rag:manage,当前租户 key 为 sakila
- WHEN 请求 default 数据源的 schema 枚举
- THEN 返回 `["tenant_sakila", "public"]`

#### Scenario: 动态数据源枚举

- GIVEN 某动态数据源 default_schema 为 sales
- WHEN 请求该数据源的 schema 枚举
- THEN 返回 `["sales"]`

#### Scenario: 数据源不可用 fail-closed

- GIVEN 请求携带的 datasource_key 不存在/已禁用/不归属当前租户
- WHEN 请求 schema 枚举
- THEN 拒绝(同形,不区分具体原因,防 key 探测)

### Requirement: 文档上传与索引

上传 Markdown 表说明(`## 表名` 二级标题切块),生成 embedding(1536 维)后
幂等写入 rag.table_descriptions;重复上传覆盖更新(upsert);无权限或超限被拒。
端点为 `POST /system/rag/documents/upload`(multipart),要求 system:rag:manage
权限 + 租户上下文(与 chat 域同构的 X-Tenant-Id 白名单复校);datasource_key
SHALL 通过存在+启用+归属当前租户校验;表单 schema_name SHALL 双闸校验——
先过 PG 标识符语法校验(`^[a-z_][a-z0-9_]{0,62}$`,与 datasource 域
default_schema 同一规则),再校验其属于该数据源的可扫描 schema 集合
(与内省扫描同源同口径,见「可扫描 Schema 枚举」),越界即 400
`SCHEMA_NOT_ALLOWED`,不落库——写边界 fail-fast,防拼错 schema 静默毒化召回;
文档标题解析出的表名/schema SHALL 小写折叠(PG 未加引号标识符语义)。
上传文件名(doc_name)SHALL 过 PII 检测(护栏 pii_* 规则集),命中敏感信息
(身份证号/手机号等)即 400 拒绝——拒绝优于脱敏(脱敏后名可能撞
uq_documents_tdq 唯一键);数字命名的正常文件(订单号等)按既有 bankcard
19/20 前缀排除口径不误拒。
知识条目以 (tenant_id, datasource_key, schema_name, table_name) 四元唯一
(baseline 既定形态,与召回过滤维度对齐)。上传来源 SHALL 标记
source_type=MANUAL_UPLOAD;embedding 输入文本 SHALL 为「表名 + 表说明 +
字段说明原文」的确定性拼接。支持扩展名:.md/.markdown/.txt(文本链路)。
上传解析 SHALL 在表块数上限(默认 500)内——超限拒绝,防大文档切块风暴;
上传属管理写操作,SHALL 落 sys_oper_log 审计(与业务同事务)。

#### Scenario: 上传表说明 Markdown 成功索引

- GIVEN 用户已登录,角色有 system:rag:manage 权限,当前租户为 sakila
- WHEN 上传含「## customer」表说明的 Markdown
- THEN 按 `##` 切分解析出表块,生成向量写入 rag.table_descriptions(tenant/datasource/schema/table 四维)
- AND 返回 200 + 落库表名列表

#### Scenario: 同表重复上传覆盖更新(幂等)

- GIVEN table_descriptions 已有 customer 表记录(同租户同数据源同 schema)
- WHEN 再次上传含 customer 的文档
- THEN 触发唯一约束后改走 UPDATE,更新说明/示例/向量/全文索引/时间戳,不产生重复行

#### Scenario: 无权限用户上传被拒

- GIVEN 用户角色无 system:rag:manage
- WHEN 上传
- THEN 返回 403

#### Scenario: 上传内容过大被拒

- GIVEN 上传文本类文档 > 1MB(配置上限)
- WHEN 上传
- THEN 返回 400(文档过大)

#### Scenario: 表块数超限被拒

- GIVEN 上传文档解析出超过表块数上限(默认 500)的 `##` 表块
- WHEN 上传
- THEN 返回 400(表块数超限),不写入知识库,不触发 embedding

#### Scenario: 上传对 datasource_key 校验 fail-closed

- GIVEN 上传请求携带的 datasource_key 不存在/已禁用/不归属当前租户
- WHEN 上传
- THEN 拒绝(同形,不区分具体原因,防 key 探测)

#### Scenario: schema_name 非法标识符被拒

- GIVEN 上传表单 schema_name 含大写/引号/空白等不匹配 `^[a-z_][a-z0-9_]{0,62}$` 的形态
- WHEN 上传
- THEN 返回 400,不落库(防畸形值直达 LLM 提示层)

#### Scenario: schema_name 不在可扫描集合被拒

- GIVEN 上传表单 schema_name 语法合法但不在该数据源可扫描集合
  (如 default 源填 `tenant_others`、拼错的 `tenant_sakila1`)
- WHEN 上传
- THEN 返回 400 SCHEMA_NOT_ALLOWED,不落库(写边界拦截,错误不上置到提问期)

#### Scenario: 表名小写折叠幂等

- GIVEN 先后两次上传 `## Customer` 与 `## customer` 表块(其余内容一致)
- WHEN 落库
- THEN 解析为同一表名,第二次走 UPDATE 覆盖,不产生重复行

#### Scenario: 切块解析失败被拒

- GIVEN 上传的 Markdown 不含任何 `## 表名` 形态二级标题
- WHEN 解析
- THEN 返回 400(提示分块格式要求),不写入知识库

#### Scenario: 文件名含 PII 被拒

- GIVEN 上传文件名为「王五_11010119900101123X_简历.md」
- WHEN 上传
- THEN 返回 400(文件名含敏感信息),不落库

#### Scenario: 数字文件名不误拒

- GIVEN 上传文件名为「orders_20260825_export.md」(纯日期/订单号形态数字)
- WHEN 上传
- THEN 正常索引(PII 规则不误伤)

### Requirement: 三路混合召回

retrieve 链路经控制面 `POST /internal/rag/retrieve`(内部令牌,Agent 面回调)
执行三路召回:①表说明向量召回(pgvector 余弦 topK,默认 5)②表说明全文检索
(tsvector,plainto_tsquery + ts_rank,BM25 近似)③query_history 历史成功 SQL
问句向量召回;①②两路用 RRF(Reciprocal Rank Fusion,k=60)融合排序后取
topK;③为独立强 few-shot 通道,SHALL NOT 参与融合(人类验证过的成功 SQL
置信度高,喂给 generate 的优先级高于表说明内嵌示例)。三路召回均 MUST 强制
tenant + datasource_key 双过滤。**实体别名(alias) SHALL 参与召回与注入:
embedding/tsv 文本拼入 alias(用户业务称谓命中 alias 提升召回;alias 命中
覆盖保留时,tsv/embedding 以生效别名(=既有别名)同源重算,派生检索文本
不得与 alias 列脱钩);schema_hint 表说明段拼装带 alias(表头行形态
`表名(别名)(列…) -- 表说明`,generate 与 summarize 共享该段,不再单开
别名段)**。embedding 服务不可用时 SHALL 降级为仅全文路(不阻断);检索
上下文缓存(Redis)SHALL fail-open——缓存不可用按未命中处理。知识写操作
(上传/删除/编辑表说明/fetch-metadata/reindex)后 SHALL 精确失效对应
tenant+datasource 的检索缓存。

#### Scenario: 表说明向量召回
- GIVEN 租户已有 customer/film/rental 表说明(已向量化)
- WHEN Agent retrieve 节点收到问句「查询所有客户」
- THEN 经控制面 /internal/rag/retrieve(内部令牌)做向量召回 topK(默认 5)
- AND customer 表说明相似度最高

#### Scenario: 业务称谓经别名命中召回
- GIVEN goods 表说明 alias=「商品表」(已向量化拼入)
- WHEN 提问「商品表有多少种商品」
- THEN goods 表说明被命中召回;schema_hint 含「goods(商品表)」形态

#### Scenario: 精确表名经全文检索修正排序
- GIVEN 有「bed_bath_table」表说明,纯向量召回因语义偏移不准
- WHEN 提问「查询 bed_bath_table 数据」
- THEN 全文检索路精确命中该关键词
- AND RRF 融合后该表排名优于纯向量召回

#### Scenario: 历史成功 SQL 作为强 few-shot
- GIVEN query_history 有「查询客户邮箱」的成功 SQL
- WHEN 新提问「客户邮箱列表」(语义相似)
- THEN query_history 路召回该 SQL,作为强 few-shot 喂给 generate 节点(优先级高于 sample_queries)

#### Scenario: 跨租户+跨数据源隔离
- GIVEN sakila 与 olist 租户/数据源都有同名表说明
- WHEN sakila 上下文召回
- THEN 三路召回都强制按 tenant + datasource_key 双过滤,不返回他租户/他数据源内容

#### Scenario: 各路空命中降级
- GIVEN 某租户知识库为空(新租户)
- WHEN 召回
- THEN 对应路返回空,RRF 融合退化为可用路,不报错
- AND 表说明两路(向量+全文)均零命中时 retrieve 节点回退实时内省兜底
(见 nl2sql-agent 域「Schema 智能选取」),不降级为空提示

#### Scenario: embedding 服务不可用降级全文路
- GIVEN Agent 面 embedding 端点超时/失败
- WHEN 召回
- THEN 向量路与历史路返回空,全文检索路照常执行,不报错不阻断

#### Scenario: 知识写操作后缓存精确失效
- GIVEN 同一问句的检索上下文已缓存(命中返回)
- WHEN 该租户该数据源发生知识写操作(上传/删除/fetch-metadata/reindex)
- THEN 对应缓存被精确失效(仅目标 tenant+datasource 范围),下次召回重建;
其余租户/数据源缓存不受影响

### Requirement: PDF/Word 文档解析(扩展上传格式)

上传格式扩展支持 PDF/Word(仅 .docx;旧 .doc 二进制格式不支持),解析为
纯文本后复用 `## 表名` 切块 → embedding → upsert 链路;加密/损坏或超大文件
被拒。解析 SHALL 与上传主链路同一权限/租户/datasource/schema 校验与表块数
上限;解析为 CPU 密集操作,SHALL 在工作线程执行不阻塞服务事件循环。

#### Scenario: 上传 PDF 解析成功并索引
- GIVEN 用户有 system:rag:manage 权限
- WHEN 上传含表说明文本的 .pdf
- THEN 服务端解析为纯文本,复用切块→embedding→upsert 链路,结果与 Markdown 上传一致

#### Scenario: 加密/损坏文件解析失败报错
- GIVEN 上传的 PDF 加密或损坏
- WHEN 解析异常
- THEN 返回 400(含原因),不写入知识库

#### Scenario: 超大文件被拒
- GIVEN 上传文件 > 10MB(PDF/Word 上限,含格式开销大于 Markdown 的 1MB)
- WHEN 上传
- THEN 返回 400(文件过大)

### Requirement: 知识库管理

提供当前租户知识条目的列表查询与删除(硬删);列表与删除端点均 SHALL 要求
system:rag:manage(与页面守卫、同域写操作权限口径一致):列表
`GET /system/rag/tables`(分页 + 可选 datasource_key 筛选,按更新时间倒序,
不返回向量与全文索引列);删除 `DELETE /system/rag/tables/{id}`,删除后触发
检索缓存失效,后续召回不再命中,并落 sys_oper_log 审计(与业务同事务——
与 fetch/reindex 同域同口径)。重建索引(reindex)能力归 knowledge 域立约
(见 knowledge spec「reindex(按数据源重建)」requirement)。

#### Scenario: 列出当前租户的表说明
- GIVEN 用户已登录且持有 system:rag:manage
- WHEN 分页查询知识列表
- THEN 返回当前 tenant(+可选 datasource 筛选)的条目(table_name/table_comment/source_type/updated_at)
- AND 不返回向量与全文索引列

#### Scenario: 删除某表说明
- GIVEN 用户有 system:rag:manage
- WHEN 删除某条
- THEN 硬删该行,后续召回不再命中,落 sys_oper_log 审计

#### Scenario: 越租户删除被拒
- GIVEN 知识条目归属其他租户
- WHEN 删除
- THEN 拒绝(同形 404,不暴露他租户条目存在性)

#### Scenario: 重建索引
- GIVEN 知识有数据但向量过时(如换 embedding 模型后)
- WHEN 触发重建索引
- THEN 按知识域 reindex 立约执行:清空当前租户+数据源全部条目并重新扫描
   (含润色),重新生成向量更新(行为契约见 knowledge 域)

### Requirement: 成功 SQL 回写 query_history

查询链路最终判定成功(status=SUCCESS)的查询 SHALL 回写 rag.query_history
(tenant/datasource/question/sql_text/embedding)——时机为查询链路完成最终
判定后(chat 域消息落库路径);question SHALL 取用户原始问句(与审计口径
一致);成功判定不依赖结果行数(空结果的成功查询同样回写)。失败/拦截查询
SHALL NOT 回写,避免错误 SQL 被召回;回写失败不阻断主流程(独立事务边界,
仅告警)。

回写 SHALL 幂等治理(消重复沉淀):(tenant, datasource_key, question) 同键
重复回写 SHALL 覆盖该行(保留最新成功 SQL,不新增行)——同题多行会重复占据
few-shot 召回位;embedding 计算失败时 SHALL 保留该行旧向量(旧向量语义仍
有效,清空会使该行退出向量召回)。每 (tenant, datasource_key) 维度 SHALL
维持总量上限(超出时淘汰 created_at 最旧行)。

#### Scenario: 成功查询自动回写

- **GIVEN** 用户提问成功(status=SUCCESS,含零行结果的成功查询)
- **WHEN** 查询链路完成最终判定
- **THEN** 同步回写 query_history(tenant, datasource_key, question=原始问句, sql_text, status=SUCCESS, embedding 复用问句向量缓存,不二次调用 embedding)
- **AND** 回写失败不阻断主流程(仅告警日志)

#### Scenario: 失败/拦截查询不回写

- **GIVEN** 查询失败或被拦截
- **WHEN** 查询链路完成最终判定
- **THEN** 不回写 query_history(只记成功 SQL,避免错误 SQL 被召回污染 few-shot)

#### Scenario: 同题重复回写幂等覆盖

- **GIVEN** 同一 (tenant, datasource_key, question) 已有成功回写行
- **WHEN** 该题再次成功回写(如反馈纠错后重查、原 SQL 再次成功)
- **THEN** 覆盖既有行(sql_text 更新为最新成功 SQL),行数不增长
- **AND** embedding 计算失败时保留旧向量,不清空

#### Scenario: 总量上限淘汰

- **GIVEN** 某 (tenant, datasource_key) 维度回写行数达到上限
- **WHEN** 新回写写入
- **THEN** 淘汰该维度 created_at 最旧行,总量不超上限

### Requirement: 召回响应纯增语义段组装

/internal/rag/retrieve 在三路召回结果之外 SHALL 追加组装语义段(semantic-layer spec):relations_hint——仅取本次召回表集合(schema_hint 命中的表)两两之间的启用关系,按「左表 JOIN 右表 ON 键」文字描述;metric_hints——问句命中 business_terms 或 metric_name 的启用指标 top-5(含 metric_name 与 sql_template 文字)。两段拼入响应纯增字段;无命中时为空串,不改变既有 schema_hint/history_sql 的任何语义。

#### Scenario: 关系段只含召回集合

- GIVEN 数据源有 20 张表关系,本次召回命中 2 表
- WHEN 组装响应
- THEN relations_hint 仅含该 2 表之间的关系行

#### Scenario: 指标段按问句命中

- GIVEN 启用指标「复购率」business_terms 含「复购」
- WHEN 问句含「复购」
- THEN metric_hints 含该指标名与口径文字

#### Scenario: 无语义数据零影响

- GIVEN 该租户+数据源无关系与指标
- WHEN 召回
- THEN 响应 relations_hint/metric_hints 为空串,schema_hint/history_sql 语义不变
