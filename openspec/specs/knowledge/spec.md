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
