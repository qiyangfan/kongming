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
别名段)**。**全文检索路的分词配置 SHALL 为部署级可配项,缺省 `simple`
(与既有行为逐字一致,零回归);配置值 SHALL 为白名单枚举(至少含 `simple`
与 `zhparser`),MUST NOT 透传白名单外的任意标识符。全文向量的写入与检索
SHALL 使用同一配置(写读同配置,单一配置单源);配置目标在库不可用
(如扩展未安装)时 SHALL fail-closed 拒启并明示缺项——MUST NOT 以降级到
其他配置的方式静默启动(降级会与存量全文向量错配)。配置变更后,存量全文
向量 SHALL 经既有重建链路重算生效(重扫/重建索引流程不变);检索上下文
缓存 SHALL NOT 跨配置复用——配置变更生效后,变更前的缓存条目 MUST NOT
参与命中。中文问句经中文分词配置 SHALL 可命中含任一分词词条的表说明
(分词条命中而非整句匹配)。**。embedding 服务不可用时 SHALL 降级为仅全文路(不阻断);检索
上下文缓存(Redis)SHALL fail-open——缓存不可用按未命中处理。知识写操作
(上传/删除/编辑表说明/fetch-metadata/reindex)后 SHALL 精确失效对应
tenant+datasource 的检索缓存。

**知识条目可见集合 SHALL 为「租户公共条目(team 归属为空)∪ 调用者所在
团队的条目」:三路召回的条目过滤 MUST 叠加该可见集合,非成员团队的条目
MUST NOT 进入召回、注入与任何提示段。检索上下文缓存键 SHALL 纳入调用者
团队维度(团队集合的确定性指纹):不同可见集合的调用者 MUST NOT 命中彼此
的缓存条目;用户团队关系变化后,新召回 MUST 反映新可见集合。生产召回
链路(agent 检索回调)SHALL 透传调用者身份(可选 user_id,缺省不携带=
纯公共形态,既有负载零回归)——团队条目在 chat 问答链的可达性按本可见
集合判定。历史 few-shot 路(query_history)SHALL 同判据:样本行携带来源
可见集合(回写/晋升时调用者的团队集合,空=公共;存量样本按公共处理),
非成员 MUST NOT 经历史路接收团队口径 SQL。**

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

- GIVEN 全文检索配置从 simple 切到 zhparser 且缓存中有旧配置条目
- WHEN 切换生效后同问句召回
- THEN 旧配置缓存条目不参与命中(键含配置,按新配置重建)

#### Scenario: 团队条目成员可召回

- GIVEN 表知识 K 归属团队 T,用户 A 是 T 成员、用户 B 不是
- WHEN A 与 B 分别经生产问答链(agent 检索回调携带调用者身份)以同问句召回
- THEN A 的召回可命中 K,B 的召回不出现 K(公共条目不受影响)
- AND 回调缺省不携带身份时(旧版 agent)按纯公共形态召回,零回归

#### Scenario: 历史样本不跨可见集合

- GIVEN 团队 T 成员 A 的问句成功执行,其 SQL 回写 query_history(样本来源
  可见集合=A 所在团队)
- WHEN 非 T 成员 B 以语义相近问句召回
- THEN 该 SQL 不进入 B 的历史 few-shot 提示段;A 与公共样本召回不受影响

#### Scenario: 缓存不跨可见集合

- GIVEN 用户 A(团队 T 成员)某问句的检索上下文已缓存(含 K)
- WHEN 用户 B(非 T 成员)以同问句召回
- THEN B 不命中 A 的缓存条目(按 B 的可见集合重建,无 K)

#### Scenario: 团队关系变化即时生效

- GIVEN 用户 A 刚被移出团队 T(A 的缓存条目仍含 T 条目)
- WHEN A 再次召回
- THEN 新召回不含 T 条目(团队关系变化反映到可见集合与缓存键)

### Requirement: 知识库管理

提供当前租户知识条目的列表查询与删除(硬删);列表与删除端点均 SHALL 要求
system:rag:manage(与页面守卫、同域写操作权限口径一致):列表
`GET /system/rag/tables`(分页 + 可选 datasource_key 筛选,按更新时间倒序,
不返回向量与全文索引列);删除 `DELETE /system/rag/tables/{id}`,删除后触发
检索缓存失效,后续召回不再命中,并落 sys_oper_log 审计(与业务同事务——
与 fetch/reindex 同域同口径)。重建索引(reindex)能力归 knowledge 域立约
(见 knowledge spec「reindex(按数据源重建)」requirement)。

**知识条目 SHALL 支持团队归属治理:归属为空=租户公共(既有行为),或指向
当前租户内真实团队。写操作(上传/编辑/归属变更/删除)对团队条目 SHALL
额外校验操作者为该团队 OWNER/MEMBER,非成员按 404 同形拒绝(不暴露条目
存在性);列表 SHALL 仅呈现公共条目与操作者所在团队条目。归属变更
(公共 ⇄ 团队)SHALL 落操作审计并失效检索缓存;团队被删除时其条目 SHALL
公共化(归属置空)并落审计——知识资产不因团队解散丢失。同表知识 SHALL
保持单一实例(唯一键不含归属维度):归属转移为原位变更,MUST NOT 产生
公共/团队双版本。**

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

#### Scenario: 非成员写团队条目 404 同形

- GIVEN 条目 K 归属团队 T,操作者持知识管理权限但非 T 成员
- WHEN 编辑/删除/归属变更 K
- THEN 404 同形拒绝(不暴露存在性),条目不变

#### Scenario: 归属公共化即时生效

- GIVEN 团队 T 被删除,其名下表知识 K 归属置空(公共化,审计可查)
- WHEN 任意租户用户召回
- THEN K 按公共条目可见

#### Scenario: 归属变更审计与缓存失效

- GIVEN 管理员(团队 OWNER)把公共条目 K 变更为团队 T 专属
- WHEN 变更提交
- THEN 落操作审计,该租户+数据源检索缓存被精确失效,非 T 成员的后续
  召回不再命中 K
