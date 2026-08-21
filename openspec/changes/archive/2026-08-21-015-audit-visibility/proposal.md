# 015 审计可视化与留存(audit-visibility)

## Why

审计两表(sys_sql_audit/sys_oper_log)自 004/007 起持续落库,但只能在数据库
里看——超管无法在产品内回答「谁问了什么、被谁改过什么」;014 反馈分析
上线后,审计是最后一个无可视化的管理域。audit spec(第一梯队平移)立约的
查询分页/行级可见性/留存归档三条主线中,查询与归档均未落地;操作日志
ip 列 baseline 已建但写入链路从未接通;010 挂账⑦(导出审计)也该随本域
清零。spec 的「敏感参数脱敏」经考古确认已满足(detail 仅 user_id),本变更
用测试锁定不再倒退。

## What Changes

**审计查询(audit 域主干)**:
1. `GET /audit/sql/page` + `GET /audit/sql/{id}`:SQL 审计分页(trace_id/
   user/tenant/verdict/时间范围筛选,created_at 倒序)与单条详情(完整
   sql_text);新权限点 `system:audit:view`(种子排序 9,仅 SUPER_ADMIN);
   行级可见性:数据范围非 ALL 强制只看本人(显式注入,不依赖列推断);
   详情非本人 403 同形。
2. `GET /audit/oper/page`:操作日志分页(同款筛选与行级可见性;action/
   user/时间范围)。
3. 操作日志 ip 链路接通:client host 经 contextvars 横切
   (ClientIpMiddleware 写入,SqlOperLogRepo.add 读取——15+ 调用点零改动,
   design D2);存量空 ip 行不回填(只增不改纪律);容器部署带
   --proxy-headers(反代下记真实客户端,R1-B6)。
4. 导出审计清零(010 挂账⑦):chat 导出端点落 sys_oper_log
   `chat.export`(fmt/行数/截断标志,同事务)。

**留存与归档**:
5. 归档函数(独立可测):超窗(默认 180 天,配置项)记录导出为
   gzip JSONL + SHA-256 manifest(每文件哈希+行数+窗口),写归档目录
   (配置项,缺省 var/audit-archive)后库内删除;两表分别归档;
   失败不阻断审计写入(异常仅错误日志,与 SqlAuditWriter 同语义)。
6. 重导入函数:manifest SHA-256 校验通过才导入,重复导入按 id 幂等
   跳过(ON CONFLICT DO NOTHING)。
7. 调度:lifespan asyncio 每日定时归档(调度器为薄壳,逻辑全在可测
   函数)+管理端点手动触发(超管,同函数);「归档纳入备份链」=归档
   目录落盘即随部署备份链(design 声明,备份为部署运维面)。

**前端**:
8. /admin/audit 第七子页(nav perm=system:audit:view):SQL 审计与
   操作日志双区(筛选/分页/详情展开完整 SQL),操作日志页含 ip 列。

## Capabilities

### New Capabilities

(无——audit spec 已平移入库,本变更是实现既有立约)

### Modified Capabilities

- `audit`: 按 spec 既有 requirements 细化落地(SQL 审计查询/操作日志/
  留存与归档——端点/权限点排序/行级可见性判定/归档文件形态与调度形态
  的 Kongming 实现口径),并修订「操作日志」requirement 增补 ip 链路
  与导出审计动作清单。
- `chat`: 「结果导出 CSV/Excel」requirement 增补——导出落 sys_oper_log
  审计(010 挂账⑦清零)。

## Non-goals

- 不做归档文件的异地加密上传(备份链是部署运维面,本变更落到「归档
  目录即入备份链」声明;对象存储=技术栈锁定外的基础设施)。
- 不做审计数据的告警/指标通道(归档失败以错误日志暴露,spec 口径;
  metrics 体系随 observability 远期)。
- 不做 sys_sql_audit 表结构变更(列已建全);不做存量空 ip 行回填。
- 不做非 ALL 用户的审计自助查询(行级可见性强制本人=spec 立约,
  但 system:audit:view 不推荐绑定 DEVELOPER——管理视角权限点)。
- 不重构 SqlAuditWriter(004 既有形态正确:独立短事务+失败不阻断)。

## Impact

- **schema**:零变更(两表列已建全)。
- **api**:`kongming/audit/` 现仅 SqlAuditWriter——扩为完整域包
  (router/service/models/ports/repository 补齐,宪法分层;import-linter
  既有「datascope/audit 禁 fastapi」契约按真实包结构调整:audit 新
  router/service 分工后契约口径对齐其他域);auth/repository SqlOperLogRepo
  add 增 ip 形参(调用点全量透传);chat router 导出端点加审计;种子
  1 权限点(排序 9);lifespan 挂每日归档调度;config 增归档窗口/目录项。
- **web**:/admin/audit 第七子页;lib/audit-api.ts 封装。
- **契约**:`make contracts` 重生成(3 新端点+归档触发端点)。
- **ADR 引用**:ADR-004(零新迁移)、ADR-005(契约重生成)、宪法
  Article III(归档失败不阻断的工程纪律)。
