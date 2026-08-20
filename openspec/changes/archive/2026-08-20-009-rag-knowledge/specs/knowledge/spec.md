# knowledge 能力域 delta(009)

## MODIFIED Requirements

### Requirement: LLM 润色元数据

元数据抓取端点为 `POST /system/rag/fetch-metadata`(body 含 datasource_key;
租户上下文 + system:rag:manage 权限)。执行时对目标数据源内省全部表(限
default_schema 的 BASE TABLE,排除系统 schema 与控制面保留表),对每张发现的表
经 Agent 面 `/internal/describe-table` 调用 LLM 润色,生成中文表说明、带类型
前缀的中文字段说明、2-3 条 schema 限定示例 SQL;润色失败 MUST 降级为纯结构
内容(英文类型注释),不阻断整批拉取。结果幂等落库,落库遵循覆盖优先级:
INTROSPECTION < AI_GENERATED < MANUAL_UPLOAD < MANUAL_EDIT < FEEDBACK_REFINED
——自动来源(抓取)SHALL NOT 覆盖更高优先级的既有条目;人工上传不受此限
(显式操作即显式覆盖)。fetch-metadata 属管理写操作,SHALL 落 sys_oper_log
审计(与业务同事务),完成后精确失效检索缓存。

#### Scenario: 抓取元数据含润色内容
- GIVEN 已注册外部数据源(存在+启用+归属当前租户)
- WHEN 抓取并润色表说明
- THEN 每张表含中文 table_comment、带类型前缀的中文 columns_comment、2-3 条 schema 限定示例 SQL
- AND source_type 标记为 AI_GENERATED(润色成功)
- AND 结果幂等落库,返回发现表数与润色成功表数供前端展示

#### Scenario: LLM 异常降级纯结构不阻断整批
- GIVEN LLM 异常/超时/解析失败
- WHEN 元数据抓取执行
- THEN 降级为纯结构内容(英文类型注释),整批不失败
- AND source_type 标记为 INTROSPECTION(降级)

#### Scenario: 覆盖优先级
- GIVEN 同一张表存在多种来源说明
- WHEN 落库
- THEN 覆盖优先级为 INTROSPECTION < AI_GENERATED < MANUAL_UPLOAD < MANUAL_EDIT
- AND FEEDBACK_REFINED(反馈精修)不低于 MANUAL_EDIT
- AND 自动来源(抓取/润色)不覆盖更高优先级既有条目(跳过该表,计为 skipped)

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
当前租户该数据源的全部表说明并重新抓取(含润色)——reindex 是显式重建,
SHALL 清空人工精修条目回到自动生成基线(与 fetch-metadata 的优先级保护不同,
此为显式声明的语义);要求 system:rag:manage 权限;数据源必须存在+启用+归属
当前租户;操作记 sys_oper_log;幂等;完成后精确失效检索缓存。

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
- THEN 必须存在+启用+归属当前租户,否则拒绝(同形,不区分具体原因)
