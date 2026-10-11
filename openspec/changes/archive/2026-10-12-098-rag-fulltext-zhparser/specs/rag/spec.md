# Spec Delta

## MODIFIED Requirements

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

**全文检索路的分词配置 SHALL 为部署级可配项,缺省 `simple`(与既有行为
逐字一致,零回归);配置值 SHALL 为白名单枚举(至少含 `simple` 与
`zhparser`),MUST NOT 透传白名单外的任意标识符。全文向量的写入与检索
SHALL 使用同一配置(写读同配置,单一配置单源);配置目标在库不可用
(如扩展未安装)时 SHALL fail-closed 拒启并明示缺项——MUST NOT 以降级到
其他配置的方式静默启动(降级会与存量全文向量错配)。配置变更后,存量全文
向量 SHALL 经既有重建链路重算生效(重扫/重建索引流程不变);检索上下文
缓存 SHALL NOT 跨配置复用——配置变更生效后,变更前的缓存条目 MUST NOT
参与命中。中文问句经中文分词配置 SHALL 可命中含任一分词词条的表说明
(分词条命中而非整句匹配)。**

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

#### Scenario: 缺省 simple 零回归

- GIVEN 部署未配置全文检索配置
- WHEN 全文路写入与检索执行
- THEN 行为与配置化之前逐字一致(同词法、同排序、同得分量纲)

#### Scenario: 中文分词命中

- GIVEN 全文检索配置为 zhparser(扩展已安装),表说明含「客户信息表」
- WHEN 中文问句「查询所有客户」进入全文路
- THEN 问句按中文分词切出「客户」词条并命中该表说明(不再整句匹配零命中)

#### Scenario: 配置不可用拒启

- GIVEN 部署配置全文检索配置为 zhparser 但扩展未安装
- WHEN 控制面启动
- THEN fail-closed 拒启并报缺项(不降级、不静默)

#### Scenario: 白名单外配置拒绝

- GIVEN 部署配置全文检索配置为白名单外标识符
- WHEN 控制面启动
- THEN fail-closed 拒启并报配置非法(标识符不进入任何 SQL)

#### Scenario: 配置变更后缓存不跨配置

- GIVEN 配置 simple 时某问句的检索上下文已缓存
- WHEN 配置切换为 zhparser 并经重建链路重算存量向量
- THEN 同问句召回不命中切换前的缓存条目(按新配置重算),
  全文路与写入侧使用同一新配置
