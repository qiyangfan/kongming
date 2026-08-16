# knowledge 能力域

## Purpose

知识库元数据智能化:元数据抓取时对发现的表调用 LLM 润色中文表/字段语义与示例 SQL(润色失败降级纯结构,不阻断整批),upsert 幂等落库且人工精修上传可覆盖,reindex 支持按数据源重建。

## Requirements

### Requirement: LLM 润色元数据

元数据抓取对每张发现的表调用 LLM 润色(经 Agent 面代理),生成中文表说明、带类型前缀的中文字段说明、2-3 条 schema 限定示例 SQL;润色失败必须降级为纯结构内容,不阻断整批拉取;结果幂等落库,人工精修上传可覆盖。

#### Scenario: 抓取元数据含润色内容
- GIVEN 已注册外部数据源
- WHEN 抓取并润色表说明
- THEN 每张表含中文 table_comment、带类型前缀的中文 columns_comment、2-3 条 schema 限定示例 SQL
- AND source_type 标记为 AI_GENERATED(润色成功)
- AND 结果幂等落库,返回润色成功表数供前端展示

#### Scenario: LLM 异常降级纯结构不阻断整批
- GIVEN LLM 异常/超时/解析失败
- WHEN 元数据抓取执行
- THEN 降级为纯结构内容(英文类型注释),整批不失败
- AND source_type 标记为 INTROSPECTION(降级)

#### Scenario: 覆盖优先级
- GIVEN 同一张表存在多种来源说明
- WHEN 落库
- THEN 覆盖优先级为 INTROSPECTION < AI_GENERATED < MANUAL_EDIT(人工精修)
- AND FEEDBACK_REFINED(反馈精修)不低于 MANUAL_EDIT

### Requirement: reindex(按数据源重建)

触发重索引须清空当前租户该数据源的全部表说明并重新抓取(含润色);要求 system:rag:manage 权限;数据源必须存在+启用+归属当前租户;操作记操作日志;幂等。

#### Scenario: reindex 连续两次结果一致
- GIVEN 超管已登录并持有 system:rag:manage 权限
- WHEN 对存在且启用且归属当前租户的数据源触发 reindex
- THEN 清空并重新抓取(含润色),返回 {deleted, rebuilt, enriched}
- AND 操作记操作日志
- AND 连续执行两次结果一致(清空+确定性重建)

#### Scenario: 无权限触发被拒
- GIVEN 角色无 system:rag:manage 权限点
- WHEN 触发 reindex
- THEN 返回 403

#### Scenario: 数据源校验
- GIVEN reindex 请求
- WHEN 校验数据源
- THEN 必须存在+启用+归属当前租户,否则拒绝

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
