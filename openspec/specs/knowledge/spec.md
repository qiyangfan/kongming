# knowledge 能力域

## Purpose

知识库元数据智能化:元数据扫描时对发现的表调用 LLM 润色中文表/字段语义与示例 SQL(润色失败降级纯结构,不阻断整批),upsert 幂等落库且人工精修上传可覆盖,reindex 支持按数据源重建。

## Requirements

### Requirement: LLM 润色元数据

元数据扫描端点为 `POST /system/rag/fetch-metadata`(body 含 datasource_key;
租户上下文 + system:rag:manage 权限)。执行时对目标数据源内省全部表(限
default_schema 的 BASE TABLE,排除系统 schema 与控制面保留表),**内省 SHALL
同时扫描列注释(col_description,009 挂账⑦清零):数据库既有列注释(如装载期
comments.sql 的人工中文注释)作为润色输入的既有注释参考传给 LLM,润色输出
仍经防御清洗(臆造列剔除),列注释缺失的列行为不变**;对每张发现的表
经 Agent 面 `/internal/describe-table` 调用 LLM 润色,生成中文表说明、带类型
前缀的中文字段说明、2-3 条 schema 限定示例 SQL,**以及业务称谓别名
alias(实体别名:用户惯用的中文短名,如「商品表」「影片分类表」——
同一次 LLM 调用附带产出,零增量调用成本;alias 缺失/畸形按该字段
为空降级,不判润色失败)**;润色失败 MUST 降级为纯结构内容(英文类型
注释),不阻断整批拉取。结果幂等落库,落库遵循覆盖优先级:
INTROSPECTION < AI_GENERATED < MANUAL_UPLOAD < MANUAL_EDIT < FEEDBACK_REFINED
——自动来源(扫描)SHALL NOT 覆盖更高优先级的既有条目;人工上传不受此限
(显式操作即显式覆盖;**alias 列例外**——上传文档无别名语法,alias 清空
非用户显式意图,上传对 alias 只补空不改写既有非空)。**alias 列适用同一
覆盖优先级:MANUAL_EDIT 的 alias 永不被自动来源覆盖;自动来源行 alias
为空时允许被 AI 提炼补齐(补齐不改写既有非空 alias;整行受保护的
MANUAL_*/FEEDBACK_REFINED 行不补齐——与整行 skip 语义一致)**。
fetch-metadata 属管理写操作,SHALL 落 sys_oper_log 审计(与业务同事务),
完成后精确失效检索缓存。

#### Scenario: 扫描元数据含润色内容
- GIVEN 已注册外部数据源(存在+启用+归属当前租户)
- WHEN 扫描并润色表说明
- THEN 每张表含中文 table_comment、带类型前缀的中文 columns_comment、2-3 条 schema 限定示例 SQL
- AND 含业务称谓别名 alias(如 goods→「商品表」)
- AND 数据库既有列注释(col_description)进入润色输入作参考
- AND source_type 标记为 AI_GENERATED(润色成功)
- AND 结果幂等落库,返回发现表数与润色成功表数供前端展示

#### Scenario: LLM 异常降级纯结构不阻断整批
- GIVEN LLM 异常/超时/解析失败
- WHEN 元数据扫描执行
- THEN 降级为纯结构内容(英文类型注释),整批不失败
- AND source_type 标记为 INTROSPECTION(降级)

#### Scenario: 覆盖优先级
- GIVEN 同一张表存在多种来源说明
- WHEN 落库
- THEN 覆盖优先级为 INTROSPECTION < AI_GENERATED < MANUAL_UPLOAD < MANUAL_EDIT
- AND FEEDBACK_REFINED(反馈精修)不低于 MANUAL_EDIT
- AND 自动来源(扫描/润色)不覆盖更高优先级既有条目(跳过该表,计为 skipped)

#### Scenario: alias 手编不被润色覆盖

- GIVEN 某表 alias 经管理面手编为「订单表」(MANUAL_EDIT)
- WHEN 再次执行扫描润色
- THEN alias 保持「订单表」不被 AI 提炼改写;alias 为空的表允许
  AI 提炼补齐

#### Scenario: 润色输出防御清洗
- GIVEN LLM 返回的润色 JSON 含臆造列名/非 SELECT 语句/超长文本
- WHEN 落库前
- THEN 仅保留真实存在的列、仅保留 SELECT 开头且不含 INTO 与中间分号的示例
(SQL 长度受限,条数 ≤3),文本超长截断
- AND 表说明与列说明全空时按润色失败降级处理

#### Scenario: 无权限触发被拒
- GIVEN 角色无 system:rag:manage 权限点
- WHEN 触发 fetch-metadata
- THEN 返回 403

#### Scenario: 数据源校验
- GIVEN fetch-metadata 请求
- WHEN 校验数据源
- THEN 必须存在+启用+归属当前租户,否则拒绝(同形,不区分具体原因)

### Requirement: 扫描对账(登记=现实)

fetch-metadata 收口时 SHALL 执行对账:该 (租户, 数据源) 下
`source_type` 属自动来源(INTROSPECTION/AI_GENERATED)且
(schema_name, table_name) 不在本次扫描集内的登记行 SHALL 删除
(删除与 upsert 同事务,审计 detail 记删除数);扫描集外的人工/反馈行
(MANUAL_UPLOAD/MANUAL_EDIT/FEEDBACK_REFINED)SHALL 保留并告警
(不可逆资产交人工裁决);内省失败(连接/凭证异常)SHALL NOT 触发对账。
对账删除后 SHALL 经既有钩子精确失效该 (租户, 数据源) 检索缓存。
响应体 SHALL 含 `removed` 字段(本次对账删除行数)。
**登记优先级判定与生效别名预读 SHALL 以 (schema_name, table_name)
二元键定位既有行**——同一数据源范围内跨 schema 同名表不得互相遮蔽。
**扫描集收录范围 SHALL 限基表(`table_type = 'BASE TABLE'`):视图
SHALL NOT 进入表结构知识**(047:视图是基表派生物,行扇出使聚合失真
——film_list AVG 被库存份数加权实证;视图样例 SQL 挤占召回 top-K 使
维度表缺位实证;基表可完整表达视图可表达的一切查询,收录即引入歧义)。
存量视图/保留表自动来源登记由本对账自动清偿,无需专门清理程序。

#### Scenario: 扫描集外自动来源孤儿被对账删除
- GIVEN default 源存在扫描范围之外的自动来源登记(如旧形态 tenant_sakila.staff,AI_GENERATED)
- WHEN 对 default 源执行 fetch-metadata(扫描集=[tenant_default, public])
- THEN tenant_sakila.staff 登记行被删除,removed 计数含该行
- AND 审计 detail 记 removed;检索缓存被失效

#### Scenario: 存量视图登记被对账清偿
- GIVEN 知识库存在视图登记行(如 tenant_sakila.film_list,AI_GENERATED)
- WHEN 对该数据源执行 fetch-metadata(扫描集只含基表)
- THEN 该视图登记行被删除(自动来源孤儿),removed 计数含该行
- AND 召回与生成上下文不再出现视图条目

#### Scenario: 扫描集外人工条目保留并告警
- GIVEN 扫描范围之外存在 MANUAL_EDIT 登记行
- WHEN 执行 fetch-metadata
- THEN 该行保留(source_type 与内容不变),服务端日志告警其数量

#### Scenario: 跨 schema 同名表不互相遮蔽
- GIVEN tenant_default.goods 为 MANUAL_EDIT、public.goods 为 AI_GENERATED(同租户同数据源)
- WHEN 执行 fetch-metadata
- THEN 优先级判定各自按 (schema, table) 定位:tenant_default.goods 整行跳过保护
- AND public.goods 按自动来源正常更新

#### Scenario: 内省失败不触发对账
- GIVEN 目标数据源连接失败
- WHEN fetch-metadata 执行
- THEN 请求失败,既有登记行全部保留(不误删)

### Requirement: reindex(按数据源重建)

端点为 `POST /system/rag/reindex`(body 含 datasource_key)。触发重索引须清空
当前租户该数据源的全部表说明并重新扫描(含润色)——reindex 是显式重建,
SHALL 清空人工精修条目回到自动生成基线(与 fetch-metadata 的优先级保护不同,
此为显式声明的语义);要求 system:rag:manage 权限;数据源必须存在+启用+归属
当前租户;操作记 sys_oper_log;幂等;完成后精确失效检索缓存。
**执行序(009 挂账⑪清零):润色计算(分钟级 LLM 调用)SHALL 在数据库写事务
外先行完成,清空(delete)与重写(insert)在同一短事务内收口——消除
「delete→润色→commit」长事务锁窗口,窗口期并发 upload 冲突键不再阻塞;
润色阶段失败(数据源不可达/整批异常)则不进入删写事务,既有条目保留。**

#### Scenario: reindex 连续两次结果一致
- GIVEN 超管已登录并持有 system:rag:manage 权限
- WHEN 对存在且启用且归属当前租户的数据源触发 reindex
- THEN 清空并重新扫描(含润色),返回 {deleted, rebuilt, enriched}
- AND 操作记操作日志
- AND 连续执行两次结果一致(清空+确定性重建)

#### Scenario: 先润色后删写
- GIVEN reindex 触发
- WHEN 执行
- THEN 全部表的内省与润色完成后才开启删写事务
- AND delete 与 insert 同事务提交,锁窗口仅为删写时长(不含 LLM 耗时)

#### Scenario: 无权限触发被拒
- GIVEN 角色无 system:rag:manage 权限点
- WHEN 触发 reindex
- THEN 返回 403

#### Scenario: 数据源校验
- GIVEN reindex 请求
- WHEN 校验数据源
- THEN 必须存在+启用+归属当前租户,否则拒绝(同形,不区分具体原因)

### Requirement: 不变式约束

召回/执行链路不变;润色内容只进文本列,不进 SQL 执行路径;手工上传的解析/校验/upsert 行为与既有文档上传一致。

#### Scenario: 润色内容不进执行路径
- GIVEN 知识条目含润色内容
- WHEN 召回/执行链路使用
- THEN 润色内容只进提示文本,不影响 SQL 防火墙与执行

#### Scenario: 手工上传行为一致
- GIVEN 手工上传精修文档
- WHEN 解析/校验/upsert
- THEN 行为与 rag 域的文档上传一致(同一链路)
### Requirement: 表说明别名治理

系统 SHALL 支持表说明实体别名(alias)的管理面治理:/admin/rag
表说明编辑 SHALL 含 alias 字段(与 table_comment 同面编辑);alias
编辑落库标记 MANUAL_EDIT 来源(适用覆盖优先级最高档);alias
响应经生成契约类型回传(禁手写类型)。

#### Scenario: 管理面编辑别名

- GIVEN 超管打开某表说明编辑
- WHEN 修改 alias 为「订单表」并保存
- THEN 落库 alias=「订单表」,source_type=MANUAL_EDIT;后续召回与
  摘要称谓以「订单表」为准

### Requirement: 知识写路径级联失效建议缓存

知识写路径(上传/编辑/删除/fetch-metadata/reindex)SHALL 在失效检索
缓存的同时级联失效该数据源的建议问题缓存(与 ctx 缓存失效同点、同一
业务事务,039 datasource 级联先例同范式)——表结构知识变化(收录收窄/
对账清偿/人工治理)若不失效建议缓存,存量建议会持续推荐已被治理排除
的问题(047 实证:保留表收编后 default 源建议仍推荐撞墙题 3/5)。

#### Scenario: fetch-metadata 对账清偿后建议缓存同步失效
- GIVEN default 源存在存量建议缓存(含已被治理排除的问题)
- WHEN 对 default 源执行 fetch-metadata 触发对账删除
- THEN 该数据源的建议缓存被同事务删除
- AND 下次访问按新知识状态重新生成建议

### Requirement: 知识覆盖报告

系统 SHALL 提供管理面只读端点 `GET /system/rag/coverage-report`(query 参数含
datasource_key;租户上下文 + system:rag:manage 权限),按 (租户, 数据源)
一次性编译知识覆盖报告。报告 SHALL 为**确定性纯聚合**:不调用 LLM、不写任何
数据、不读写任何缓存;数据源校验与 fetch-metadata 同形(存在+启用+归属当前
租户,否则同形拒绝,不区分具体原因)。

报告 SHALL 盘点四类知识空白:

1. **零覆盖表**:内省扫描集(与 fetch-metadata 同语义:default schema 的
   BASE TABLE,排除系统 schema 与控制面保留表)中存在真实结构但无表知识
   登记的表,按 (schema, table) 列出;
2. **纯结构登记表**:已登记但来源为 INTROSPECTION(润色降级,零语义内容)
   的表;
3. **缺标签列**:已登记表的内省列名集合与其知识条目列标签键集合的差集,
   按表聚合缺失列清单与缺失计数;
4. **失败问题簇**:该数据源会话近 30 天消息中状态为 FAILED/REJECTED/
   NO_DATA、差评(rating 为 DOWN)或已有纠正 SQL 的问题,按归一化问题文本
   (去全部空白、忽略大小写)去重归并,每簇给出出现次数、最近状态与
   最近时间;NO_DATA(如实弃答)作为知识未接住的弱信号计入,状态分列呈现。

报告 SHALL 附 few-shot 通道概况:该 (租户, 数据源) 的成功沉淀条数、去重
问题数与上限占用率,以及示例 SQL 为空的登记表清单。

各清单条目 SHALL 封顶(零覆盖表/纯结构登记 ≤200、缺标签列按表 ≤100、失败
问题簇 ≤50),封顶时总数计数 SHALL 如实反映全量不受封顶影响。内省失败
(连接/凭证异常)SHALL NOT 产出半份报告,整体按失败返回(宁缺毋错,与扫描
对账的内省失败口径一致)。报告为纯读操作,SHALL NOT 触发审计写、缓存失效或
任何检索/生成链路行为。

#### Scenario: 四类空白盘点

- GIVEN 某数据源存在 1 张未登记的基表、1 张 INTROSPECTION 来源登记表、
  1 张登记表缺 3 个列标签且示例 SQL 为空
- WHEN 持权触发覆盖报告
- THEN 零覆盖表列出该未登记基表(schema+table)
- AND 纯结构登记表列出该 INTROSPECTION 来源表
- AND 缺标签列按表列出 3 个缺失列,缺失计数为 3
- AND 示例缺口列出该示例 SQL 为空的登记表

#### Scenario: 失败问题簇归并

- GIVEN 该数据源近 30 天内同一问题文本(仅空白/大小写差异)失败 2 次,
  另一问题获差评 1 次
- WHEN 触发覆盖报告
- THEN 失败问题簇中前者归并为 1 簇且出现次数为 2、最近状态为 FAILED
- AND 差评问题独立成簇并带其差评依据

#### Scenario: 内省失败不产出半份报告

- GIVEN 目标数据源连接失败
- WHEN 触发覆盖报告
- THEN 请求失败返回,不返回仅含失败问题簇的部分报告
- AND 既有数据零改动

#### Scenario: 无权限触发被拒

- GIVEN 角色无 system:rag:manage 权限点
- WHEN 触发覆盖报告
- THEN 返回 403

#### Scenario: 数据源校验同形拒绝

- GIVEN 覆盖报告请求
- WHEN 校验数据源
- THEN 必须存在+启用+归属当前租户,否则拒绝(同形,不区分具体原因,
  防数据源 key 探测);他租户数据源内容不可见

#### Scenario: 确定性纯读无副作用

- GIVEN 同一 (租户, 数据源) 连续两次触发覆盖报告且底层无知识变更
- WHEN 比对两次响应
- THEN 报告内容一致(确定性聚合)
- AND 全程无 LLM 调用、无数据写入、无缓存失效、无检索/生成链路行为

#### Scenario: 大清单封顶计数如实

- GIVEN 某数据源存在 300 张零覆盖表
- WHEN 触发覆盖报告
- THEN 零覆盖表清单条目封顶 ≤200
- AND 该节总数计数如实为 300
