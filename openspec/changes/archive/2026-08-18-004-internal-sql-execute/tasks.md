# 004 任务清单

> 安全规则类测试先行(config.yaml):T1→T4 先写失败测试,再实现。
> 每个 task 完成跑对应门禁子集并记录。

## T1 迁移 0003:sys_dept + sys_user.dept_id(ADR-004)

- [x] `alembic/versions/0003_dept.py`:建 sys_dept(UUID v7 PK、parent_id
  自引用 NULL=根、dept_name、tenant_id FK、sort、status VARCHAR+CHECK、
  created_at/updated_at)+ sys_user.dept_id(NULL FK)+ idx(parent_id)/
  idx(sys_user.dept_id);downgrade 删列删表
- [x] conftest TRUNCATED 清单补 sys_dept(顺带补 sys_sql_audit)
- [x] 验证:`uv run alembic upgrade head`(dev 库 0002→0003 成功)+
  `uv run pytest -x` 存量 70 测试零回归(70 passed)

## T2 datascope 十条清单失败测试先行(宪法 Article III)

- [x] `tests/test_datascope.py`:按 spec「单元测试覆盖」十条逐条立用例
  (ALL 不改写 / DEPT 改写 / DEPT 无列降级 / DEPT_AND_BELOW 递归 IN /
  无子部门 IN 单元素 / SELF 改写 / SELF 无列降级 / 复杂 SQL 改写语法正确 /
  CUSTOM fail-closed / 改写后再过防火墙),另补:多角色取最宽松、
  未绑定部门降级、UNION/FROM 子查询降级、绑定参数非拼接断言(落稿 25 → 审查循环后 30 用例)
- [x] 确认全部失败(模块不存在,collection error)

## T3 datascope 域包实现

- [x] `kongming/datascope/models.py`:DataScope 枚举别名、宽松序 ranking、
  ScopeContext、ScopeFilter(SKIP/USER_EQ/DEPT_IN/UNSUPPORTED)、
  AppliedSQL、DataScopeUnsupported 错误
- [x] `kongming/datascope/service.py`:resolve(ctx, column_exists) 纯逻辑
  (零框架 import)
- [x] `kongming/datascope/rewrite.py`:sqlglot 包子查询改写 + 主表名提取 +
  命名占位参数(D1/D2;postgres 方言占位符渲 pyformat,谓词常量形态手拼
  保留 :name 风格)
- [x] `kongming/datascope/repository.py`:用户角色 scope 集、用户 dept_id、
  递归 CTE 子部门、information_schema 列探测(带 data_type)
- [x] applier.py 编排:用户/角色缺失降级、ALL 短路、类型感知绑定(uuid)
- [x] T2 全绿(25 passed)+ ruff/mypy strict 过

## T4 执行路由失败测试先行

- [x] `tests/test_internal_execute.py`(真实 PG 全栈,落稿 15 → 审查循环后 17 用例):
  未知租户 404 / 禁用租户 404 / 用户未绑定租户 403 / 非 default 数据源 400 /
  无内部令牌 403 / 系统调用豁免 + search_path 路由(双租户同名表隔离)/
  limit 截断 / 连接级只读(current_setting=on)/ DELETE BLOCKED 落审计 /
  列名错误 ERROR 含 DB 摘要 / PASS 审计全字段 / SELF 改写收敛+改写形态落库 /
  CUSTOM fail-closed BLOCKED / 改写污染(monkeypatch 注 DDL)复检拦截
- [x] 确认失败(13 failed,1 passed——middleware 令牌回归天然通过)

## T5 审计 writer + 执行编排 + 路由真实化

- [x] `kongming/audit/`:models(SqlAuditEntry)+ SqlAuditWriter(独立短事务,
  失败 error 日志不阻断;trace_id/block_reason 超长截断告警)
- [x] `kongming/internal/executor.py`:只读引擎(server_settings
  default_transaction_read_only=on + statement_timeout=30s,pool 5/0,
  按 key 惰性建,dispose 钩子预留动态池失效)
- [x] `kongming/internal/service.py`:编排(租户解析→绑定复校→防火墙→
  data_scope→复检→SET LOCAL search_path→fetchmany 截断→审计),
  三态 verdict;ERROR reason 剥 SQLAlchemy asyncpg 包装类前缀
- [x] `internal/router.py`:请求/响应模型落定(tenant_key/question/verdict/
  reason/columns/rows/row_count/duration_ms),404/403/400 错误映射
- [x] T4 全绿(15 passed;审查循环后 17)
- [x] 设计修正:改写机制由「包外层子查询」改为「最外层 WHERE AST 注入」
  (集成测试证伪前者:投影不含过滤列时外层不可见;spec delta 已修正措辞)

## T6 门禁与契约

- [x] `make check` api 子集全绿(ruff+mypy+lint-imports+pytest 111)
- [x] import-linter 新增 datascope/audit 禁 fastapi 约定(pyproject,
  include_external_packages=true);Makefile api/agent-check 接线 lint-imports
  (原仅 CI 运行,本地门禁补齐)
- [x] `make contracts` 导出 + 前端类型重生成(diff 仅内部路由模型;web tsc 过)
- [x] 全量 `make check`(api 111 + agent 6 + web tsc/eslint 全绿)

## T7 冒烟与文档

- [x] 手工冒烟(2026-08-18,dev 库 tenant_default.goods 造数):
  PASS(系统调用 3 行)/ BLOCKED(DELETE,reason=BLOCKED_ROOT)/ ERROR
  (列不存在,reason 整洁含 HINT)/ 无令牌 403 / admin(ALL)limit=2 截断 /
  SELF 用户只见本人行(连弩),审计 sql_text 为改写后绑定参数形态;
  sys_sql_audit 六行三类 verdict 齐全,trace_id 贯穿
- [x] HANDOFF.md 更新当前状态/下一棒;spec sync 待 archive
