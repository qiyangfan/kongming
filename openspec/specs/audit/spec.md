# audit 能力域

## Purpose

审计体系:SQL 审计(sys_sql_audit)与操作日志(sys_oper_log)的落库契约、可视化查询、行级可见性、留存与归档。

## Requirements

### Requirement: 查询审计落库(完整性与贯穿)

每次 NL2SQL 查询 MUST 落审计:trace_id MUST 贯穿 控制面→Agent→执行 回调;成功、失败、被拦截三类都 MUST 落库,保证审计完整。

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

系统 SHALL 提供审计分页查询端点 `GET /audit/sql/page`(权限点
`system:audit:view`,种子排序 9,仅 SUPER_ADMIN 自动覆盖,不推荐绑定
DEVELOPER)与单条详情端点 `GET /audit/sql/{auditId}`。分页筛选参数
(trace_id/user_id/tenant_id/verdict/时间范围)均可空,按 created_at
倒序(id DESC 时序 tiebreak);page≥1、size 1..100 缺省 20。
行级可见性按数据范围收敛:非 ALL 显式强制只看本人(接口内显式注入
user_id 过滤,不依赖通用机制对审计表的列推断;判定=角色集是否含
data_scope=ALL,与 feedback 域同一口径);单条详情含完整 sql_text
(非截断),非 ALL 且记录非本人返回 403 同形(不区分「不存在」与
「无权」)。无权限访问 403。

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
- AND 数据范围非 ALL 且记录非本人 → 403(同形,不泄露记录存在性)

### Requirement: 操作日志

管理操作自动记录到 sys_oper_log(user/action/target/detail/ip/时间),主流程不受记录失败影响;敏感参数脱敏;行级可见性同审计口径。
系统 SHALL 提供操作日志分页查询端点 `GET /audit/oper/page`(权限点
`system:audit:view`;筛选 action/user_id/时间范围均可空,created_at
倒序;行级可见性同 SQL 审计查询口径——非 ALL 强制只看本人)。
**ip 链路(015 接通,列在 baseline 已建)**:操作日志写入 SHALL 记录
调用方 client host(请求上下文透传至审计写入,不经逐端点手工拼装);
存量空 ip 行不回填(只增不改)。
**导出审计(010 挂账⑦清零)**:chat 结果导出端点 SHALL 落
`chat.export` 动作(与业务同事务;detail 含 fmt/rows/truncated)。
动作清单测试全量断言锁定(007 先例);密码等敏感字段永不入 detail
(现状已满足,测试锁定不倒退)。

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

#### Scenario: 操作日志分页查询
- GIVEN 持有 system:audit:view
- WHEN GET /audit/oper/page?action=&user_id=&start_time=&end_time=
- THEN 分页返回,筛选均可空,按 created_at 倒序
- AND 数据范围非 ALL 强制只看本人

### Requirement: 审计留存窗口与热数据在线查询

审计类日志(SQL 审计、操作日志)留存不少于 6 个月:库内保留最近 180 天热数据
供在线查询,超窗记录由定时归档任务导出为压缩归档文件后删除。归档窗口默认
180 天(配置项 `audit_archive_days`)。归档任务失败 SHALL NOT 阻断审计写入,
失败以错误日志与指标暴露。

#### Scenario: 过期数据自动归档
- GIVEN 审计表中存在早于 180 天的记录
- WHEN 归档任务按计划执行
- THEN 过期记录导出为带 SHA-256 清单的压缩文件,随后库内删除,库内仅剩窗口内数据

#### Scenario: 归档失败不阻断审计写入
- GIVEN 归档任务失败(如对象存储不可达)
- WHEN 用户执行新的查询
- THEN 审计正常写入,任务失败以错误日志与指标暴露

### Requirement: 归档文件形态与导出校验

**Kongming 实现形态(015 细化)**:归档目录配置项 `audit_archive_dir`
(缺省 var/audit-archive,落盘即随部署备份链——「纳入备份链」以目录声明,
异地加密上传为部署运维面)。归档文件形态 SHALL 为每表一个 gzip JSONL
(一行一记录)+ manifest.json(含每文件 SHA-256/行数/窗口起止/导出时间)。
删除 SHALL 在导出校验(manifest 写盘成功)后执行:导出与删除分步,
删除失败保留归档文件。归档文件产生后 SHALL 随当日备份加密上传异地,
完整性校验覆盖归档清单。

#### Scenario: 归档纳入备份链
- GIVEN 归档任务产生新归档文件
- WHEN 当日备份执行
- THEN 归档文件随备份加密上传异地,完整性校验覆盖归档清单
- AND Kongming 形态=归档目录落盘即随部署备份链(异地加密为部署运维面,design 声明)

### Requirement: 归档重跑语义与重导入

重导入 SHALL 按 id 幂等(ON CONFLICT DO NOTHING),manifest SHA-256 校验失败
SHALL 拒绝导入。同日重跑幂等:窗口目录=导出日,manifest 完整且导出范围覆盖
本次删除范围才跳过导出——days 调小后旧 manifest 不覆盖新增段,必须重新导出
(R1-S2)。跨日重跑(前次删除失败的补删)可能向新目录重复导出同一批行,
恢复按 id 幂等去重兜底(R1-S4:以导出日为窗口锚并显式声明跨日重复语义,
恢复侧 ON CONFLICT 消重)。

#### Scenario: 归档文件可重导入
- GIVEN 一份归档文件及其清单
- WHEN 执行重导入
- THEN 记录恢复至可查询状态,SHA-256 校验通过
- AND 重复导入按 id 幂等跳过,不产生重复行

#### Scenario: 重导入校验失败拒绝
- GIVEN 归档文件内容与其 manifest 的 SHA-256 不符
- WHEN 执行重导入
- THEN 拒绝导入,不产生半截或错误数据

### Requirement: 归档与重导入的调度与管理端点

归档调度 SHALL 为 lifespan asyncio 每日定时(调度器为薄壳,归档逻辑全部在
独立可测函数);并 SHALL 提供管理端点 `POST /audit/archive/run`
(system:audit:view,超管手动触发同函数,返回 {exported, deleted, files})。
重导入 SHALL 为管理端点 `POST /audit/archive/restore`(同权限点,
返回 {restored, skipped})。

#### Scenario: 超管手动触发归档
- GIVEN 超管持有 system:audit:view
- WHEN 调用 POST /audit/archive/run
- THEN 执行与定时任务同一归档函数,返回 {exported, deleted, files}

#### Scenario: 手动重导入返回计数
- GIVEN 一份已校验通过的归档文件
- WHEN 调用 POST /audit/archive/restore
- THEN 返回 {restored, skipped},跳过行按 id 已存在统计



#### Scenario: 过期数据自动归档
- GIVEN 审计表中存在早于 180 天的记录
- WHEN 归档任务按计划执行
- THEN 过期记录导出为带 SHA-256 清单的压缩文件,随后库内删除,库内仅剩窗口内数据

#### Scenario: 归档文件可重导入
- GIVEN 一份归档文件及其清单
- WHEN 执行重导入
- THEN 记录恢复至可查询状态,SHA-256 校验通过
- AND 重复导入按 id 幂等跳过,不产生重复行

#### Scenario: 归档失败不阻断审计写入
- GIVEN 归档任务失败(如对象存储不可达)
- WHEN 用户执行新的查询
- THEN 审计正常写入,任务失败以错误日志与指标暴露

#### Scenario: 归档纳入备份链
- GIVEN 归档任务产生新归档文件
- WHEN 当日备份执行
- THEN 归档文件随备份加密上传异地,完整性校验覆盖归档清单
- AND Kongming 形态=归档目录落盘即随部署备份链(异地加密为部署运维面,design 声明)
### Requirement: 操作者身份可读性

审计列表(SQL 审计与操作日志)的操作者列 SHALL 以可读身份呈现:昵称缺失时
SHALL 回退登录用户名(username,随查询一并返回,契约纯增);操作者 id 为空
(系统行为)SHALL 呈现「系统」;id 存在但昵称与用户名皆缺 SHALL 呈现
「已注销」;SHALL NOT 渲染用户 id 或其截断片段。悬停提示 SHALL 与主文案同源。

#### Scenario: 昵称缺失回退用户名
- GIVEN 审计操作者未设置昵称且有登录名 op_x
- WHEN 审计列表渲染
- THEN 操作者列呈现 op_x,SHALL NOT 出现 id 截断片段

#### Scenario: 系统行为与已注销
- GIVEN 某审计行操作者 id 为空;另一行操作者昵称与用户名皆缺
- WHEN 列表渲染
- THEN 前者呈现「系统」,后者呈现「已注销」
