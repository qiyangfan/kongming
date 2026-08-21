# audit 能力域 delta(015)

## MODIFIED Requirements

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

### Requirement: 审计留存与归档

审计类日志(SQL 审计、操作日志)留存不少于 6 个月:库内保留最近 180 天热数据供在线查询,超窗记录由定时归档任务导出为压缩归档文件(含 SHA-256 清单)后删除;归档纳入备份链;可重导入;归档失败不阻断审计写入。
**Kongming 实现形态(015 细化)**:归档窗口默认 180 天(配置项
`audit_archive_days`);归档目录配置项 `audit_archive_dir`(缺省
var/audit-archive,落盘即随部署备份链——「纳入备份链」以目录声明,
异地加密上传为部署运维面);归档文件形态 SHALL 为每表一个 gzip JSONL
(一行一记录)+ manifest.json(含每文件 SHA-256/行数/窗口起止/
导出时间);删除在导出校验(manifest 写盘成功)后执行,导出与删除
分步,删除失败保留归档文件;同日重跑幂等(窗口目录=导出日,manifest
完整且导出范围覆盖本次删除范围才跳过导出——days 调小后旧 manifest
不覆盖新增段,必须重新导出,R1-S2);跨日重跑(前次删除失败的补删)可能
向新目录重复导出同一批行,恢复按 id 幂等去重兜底(R1-S4:以导出日为
窗口锚并显式声明跨日重复语义,恢复侧 ON CONFLICT 消重);重导入按
id 幂等(ON CONFLICT DO NOTHING),
manifest SHA-256 校验失败拒绝导入;调度=lifespan asyncio 每日定时
(调度器为薄壳,归档逻辑全部在独立可测函数)+管理端点
`POST /audit/archive/run`(system:audit:view,超管手动触发同函数,
返回 {exported, deleted, files});重导入为管理端点
`POST /audit/archive/restore`(同权限点,返回 {restored, skipped})。

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
