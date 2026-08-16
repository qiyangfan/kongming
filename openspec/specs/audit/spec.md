# audit 能力域

## Purpose

审计体系:SQL 审计(sys_sql_audit)与操作日志(sys_oper_log)的落库契约、可视化查询、行级可见性、留存与归档。

## Requirements

### Requirement: 查询审计落库(完整性与贯穿)

每次 NL2SQL 查询必须落审计:trace_id 贯穿 控制面→Agent→执行 回调;成功、失败、被拦截三类都落库,保证审计完整。

#### Scenario: 成功查询落审计
- GIVEN 用户执行了一次成功查询
- WHEN 查询完成
- THEN 审计行含 trace_id, user_id, tenant_id, datasource_key, question, sql_text, verdict=PASS, row_count, duration_ms, created_at

#### Scenario: 被拦截的查询落审计
- GIVEN SQL 被防火墙拒绝
- THEN 审计行 verdict=BLOCKED,block_reason 含拦截原因(如 FORBIDDEN_DDL)
- AND 拦截也计入审计,可按 verdict=BLOCKED 筛出

#### Scenario: trace_id 全链路贯穿
- GIVEN 一次多轮重试的查询(selfheal)
- WHEN 查询结束
- THEN 该次问答的全部校验/执行尝试共享同一 trace_id,可完整回放

### Requirement: SQL 审计查询

审计分页查询支持筛选(trace_id/user/tenant/verdict/时间范围);行级可见性按数据范围收敛:非 ALL 显式强制只看本人;单条详情含完整 SQL。

#### Scenario: 超管查看全部审计
- GIVEN 数据范围 ALL + system:audit:view 权限
- WHEN 分页查询(无筛选)
- THEN 返回全部审计(分页),每条含 question/sql_text/verdict/duration_ms/user/tenant/created_at

#### Scenario: 按筛选条件查询
- GIVEN 超管已登录
- WHEN 携带 userId/verdict/时间范围等筛选
- THEN 返回符合全部条件的记录

#### Scenario: 非 ALL 数据范围只看本人
- GIVEN 数据范围 SELF
- WHEN 分页查询
- THEN 强制附加「仅本人」过滤(接口内显式注入,不依赖通用机制对审计表的列推断)

#### Scenario: 无权限访问被拒
- GIVEN 角色无 system:audit:view
- WHEN 查询
- THEN 返回 403

#### Scenario: 单条详情防越权
- GIVEN 用户有 system:audit:view
- WHEN 查看单条详情
- THEN 返回完整 sql_text(非截断)
- AND 数据范围非 ALL 且记录非本人 → 403

### Requirement: 操作日志

管理操作自动记录到 sys_oper_log(user/action/target/detail/ip/时间),主流程不受记录失败影响;敏感参数脱敏;行级可见性同审计口径。

#### Scenario: 管理操作自动记录
- GIVEN 用户登录态正常
- WHEN 执行管理操作(用户 CRUD/角色绑定/知识删除/数据源管理)
- THEN 自动记录 user_id/action/target/detail/ip/created_at
- AND 记录失败不影响主流程(告警日志)

#### Scenario: 敏感参数脱敏
- GIVEN 操作涉及密码字段(如重置密码)
- WHEN 记录 detail
- THEN 密码字段值替换为「***」,原始密码不入操作日志

#### Scenario: 操作日志可见性
- GIVEN 数据范围 SELF 用户查看操作日志
- THEN 仅返回本人记录;ALL 可看全部

### Requirement: 审计留存与归档

审计类日志(SQL 审计、操作日志)留存不少于 6 个月:库内保留最近 180 天热数据供在线查询,超窗记录由定时归档任务导出为压缩归档文件(含 SHA-256 清单)后删除;归档纳入备份链;可重导入;归档失败不阻断审计写入。

#### Scenario: 过期数据自动归档
- GIVEN 审计表中存在早于 180 天的记录
- WHEN 归档任务按计划执行
- THEN 过期记录导出为带 SHA-256 清单的压缩文件,随后库内删除,库内仅剩窗口内数据

#### Scenario: 归档文件可重导入
- GIVEN 一份归档文件及其清单
- WHEN 执行重导入
- THEN 记录恢复至可查询状态,SHA-256 校验通过

#### Scenario: 归档失败不阻断审计写入
- GIVEN 归档任务失败(如对象存储不可达)
- WHEN 用户执行新的查询
- THEN 审计正常写入,任务失败以错误日志与指标暴露

#### Scenario: 归档纳入备份链
- GIVEN 归档任务产生新归档文件
- WHEN 当日备份执行
- THEN 归档文件随备份加密上传异地,完整性校验覆盖归档清单
