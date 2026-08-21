# 015 审计可视化与留存 — 设计

## Context

动机见 proposal.md。实现期已核明的现状事实:

- `kongming/audit/` 现仅 models.py(SqlAuditEntry)+ writer.py
  (SqlAuditWriter 独立短事务,失败不阻断——004 形态正确,不动);
  查询面/归档面全空白。
- `sys_oper_log.ip` 列 baseline 已建(VARCHAR(50)),SqlOperLogRepo.add
  的 INSERT 不含 ip——写入链路从未接通;调用点散布 system/rag/
  datasource/feedback 四域 15+ 处,逐点手传不可行。
- import-linter 既有契约「datascope/audit 域不依赖 Web 框架」
  source_modules=["kongming.datascope","kongming.audit"] forbidden=fastapi
  ——audit 加 router 后整包禁 fastapi 自相矛盾,契约须按真实包结构调整。
- chat export_result 目前零 DB 写(仅租户复校读),审计落库需引入
  写操作+commit(010 挂账⑦)。
- 归档无既有设施;config 无归档项。

## Goals / Non-Goals

**Goals:**
- 审计两表查询分页+详情(行级可见性非 ALL 强制本人),ip 链路接通,
  导出审计清零,180 天归档可重导入,/admin/audit 第七子页。
- 归档逻辑 100% 在独立可测函数(调度器/端点为薄壳)。

**Non-Goals:**
- 归档异地加密上传(部署运维面)、metrics 通道、存量空 ip 回填、
  非 ALL 用户自助查询权限点推广。

## Decisions

### D1. audit 域包扩展与契约调整

audit 域补 ports.py(AuditRepo Protocol)/repository.py(查询 SQL)/
service.py(行级可见性+编排,零框架)/router.py(薄适配)/archive.py
(归档纯逻辑)。models/writer 不动。import-linter 契约调整:既有
「datascope/audit 域不依赖 Web 框架」中 audit 移出(datascope 保留
原契约——该域无 router);新增「audit 领域模型/用例/端口不依赖框架」
source=[audit.models, audit.service, audit.ports] forbidden=
fastapi+sqlalchemy(与其他域同构;writer/repository/archive 为
基础设施不进契约)。

### D2. ip 链路 = contextvars 横切(零调用点改动)

`kongming/audit/context.py`:模块级 `client_ip_var: ContextVar[str|None]`;
`audit/context.py` 提供 `ClientIpMiddleware`(纯 ASGI 形态或
BaseHTTPMiddleware),每请求写入 `request.client.host`(无 client 信息
时 None);lifespan 外注册于 main.py。SqlOperLogRepo.add 读取 contextvar
填 ip 列——**全部 15+ 调用点零改动**(审计 ip 是横切关注点,逐端点
手传必漏);BackgroundTasks/定时任务无请求上下文自然为 None(语义
正确:非用户请求无 ip)。TestClient host="testclient" 可断言。

- 备选:逐端点 Request 注入+service 形参透传——否决:15+ 调用点
  全改且未来新增调用点必漏,横切关注点正解是 context。
- contextvars 在 async 任务链传播:FastAPI 请求处理在同一 task 链
  (Depends→service→repository)contextvar 可见;BackgroundTasks
  新 task 继承上下文副本(Python 语义)——标题改写等后台任务读到
  请求 ip 属合理(操作由该请求发起)。

### D3. 查询端点 = repository CAST 形态 + service 强制覆盖

repository:sys_sql_audit/sys_oper_log 分页(trace_id/action/user_id/
tenant_id/verdict/时间范围,全部 CAST(:x AS ...) 可空绑定,014 教训)+
created_at 倒序 id DESC tiebreak+count 同款 WHERE;详情按 id 取
(完整 sql_text)。service:`_visibility_user_id`(角色集 bool_or(ALL)
自备 SQL,014 同款);详情非本人 403 同形。

### D4. 导出审计 = chat 域组合根注入 OperLogger 端口

chat/ports.py 加 `OperLogger(Protocol): add(user_id, action, target,
detail)`;ChatService 构造加 `oper_logger: OperLogger | None = None`
(可选注入,与 history_writer 同范式);export_result 在格式转换后
log(`chat.export`, target=session_id?——导出无会话锚点,target=None,
detail={fmt, rows, truncated})+commit(经 repo.commit())。router
组合根绑定 `SqlOperLogRepo`(auth 域实现,007 组合模式)。
审计写失败仅告警不阻断导出(audit spec 铁律)——try 包裹与 004
SqlAuditWriter 同语义。

### D5. 归档 = 独立函数 + 薄壳调度 + 幂等重导入

`audit/archive.py`:

- `async def run_archive(*, days, archive_dir, now) -> ArchiveResult`:
  纯逻辑,依赖注入 session 工厂(测试直调);窗口 cutoff=now-days;
  两表分别:SELECT 超窗行(created_at ASC 全量)→ 写
  `<dir>/<window_tag>/sql_audit.jsonl.gz` 与 `oper_log.jsonl.gz`
  (一行一 JSON,datetime ISO 化)→ manifest.json(文件 SHA-256/
  行数/窗口起止/导出时间/表名)→ manifest 写盘成功后 DELETE 超窗行
  (同事务两表各自 commit);返回 {exported, deleted, files}。
  同日窗口目录已存在且 manifest 完整且导出范围覆盖本次删除范围
  (manifest_cutoff >= 当前 cutoff,R1-S2)→ 跳过导出仅补删;跨日重跑
  向新导出日目录导出(可能重复,恢复幂等去重兜底,R1-S4);删除失败
  保留文件(下轮重删)。窗口锚=导出日(%Y%m%d)。
- `async def run_restore(*, window_dir) -> RestoreResult`:读 manifest
  校验每文件 SHA-256(不符即拒)→ 逐行 INSERT ON CONFLICT DO
  NOTHING(id 幂等)→ 返回 {restored, skipped}。
- 文件名 window_tag=导出日(`yyyymm%d`);配置
  `audit_archive_days=180`/`audit_archive_dir="var/audit-archive"`
  (prod 无强校验,目录懒创建)。
- 调度:`_daily_archive_loop()`(lifespan create_task):睡到次日
  03:00→run_archive→循环;一切异常仅 error 日志(宪法 Article III);
  TestClient lifespan 不等待该任务(create_task 即发即忘,关闭时
  cancel)。
- 并发互斥(R1-S3):模块级 asyncio.Lock——手动端点与每日调度并发跑
  同窗口目录会 gzip "wt" 截断交错致文件损坏且删除后不可恢复,进程内
  锁序列化(单 worker 前提;多 worker 需 PG advisory lock,挂账)。
- 端点:POST /audit/archive/run(手动触发同函数)/POST
  /audit/archive/restore(body 含 window 目录名,传输层防路径穿越
  ——仅允许 `^[\w-]+$` 且必须落在 archive_dir 内)。

### D6. 前端 = /admin/audit 第七子页双区

AdminLayout 子导航加「审计」(perm=system:audit:view);页内 toggle-group
(013 组件复用)切 SQL 审计/操作日志两区:筛选(SQL 审计=trace_id/
verdict/时间;操作日志=action/时间——user 筛选接口已备、UI 未做,
挂账)+分页表+行内展开详情
(完整 sql_text/detail JSON 等宽 pre)。lib/audit-api.ts 封装(类型全生成)。

## Risks / Trade-offs

- [contextvar 泄漏跨请求] → middleware 每请求覆写(set 不 reset),
  请求间 contextvar 随 task 销毁;测试串行无 xdist。
- [归档大表全量 SELECT 内存] → 180 天窗外行数=数据规模线性;单表
  百万行 JSONL 序列化内存可控(行级流式写 gzip,yield per row fetchmany
  分批);超窗行数通常在万级,一次性 SELECT 亦可——取 fetchmany 5000
  分批稳妥。
- [恢复导入与在线查询重叠窗口] → ON CONFLICT DO NOTHING 幂等;
  窗口内既有行不覆盖(归档语义=只读历史)。
- [lifespan 调度在多 worker 重复跑] → 单 worker 部署前提(008 挂账②
  同款);归档幂等,重复跑无害(跳过导出)。
- [D4 commit 与导出只读语义] → export_result 从纯读变「审计写+commit」;
  审计写失败 try 兜底不阻断(与 004 同),事务内无其他未决写。

## Migration Plan

零 schema 迁移。种子 system:audit:view(排序 9)lifespan 幂等播种;
ip 链路随部署即生效(新行带 ip,存量空行不回填);归档目录懒创建。
契约重生成产物随代码提交。回滚=还原代码;归档文件落盘不受影响
(重导入可手工恢复)。

## Open Questions

(无——归档形态/调度形态/ip 形态均在 design 钉死。)
