# 053-sql-guard-engine 实施任务

> 纪律:安全规则类测试先行(宪法 Article III,先红后绿);每步 `make check` 对应子集全绿方可勾选;信任边界不变(ADR-002),import-linter 契约不得放宽。

## 1. 前置 gate(外部引擎仓)

- [x] 1.1 引擎独立新仓 km-sqlguard v0.1 可用:单遍 API(防火墙判定 + schema 作用域 + 保留表 + 行级改写结构)、PG 方言、解析深度上限受控、对拍 fixture 导出;验证:引擎仓自身 CI 全绿,fixture 导出命令可重复运行。

## 2. 对拍基线冻结(测试先行,先红)

- [x] 2.1 从现有 sqlfw/datascope 测试与 evals 冻结对拍期望集(含 verdict 三态、改写结果、深嵌套样本);验证:对拍集生成脚本重复运行 diff 为空。
- [x] 2.2 引擎影子模式测试:同一 SQL 双实现并行判定,断言不一致记录结构完整;验证:先红(引擎未接入),红即记录。

## 3. 依赖接入与自检

- [x] 3.1 两进程 pyproject 引入引擎 wheel,api/agent 启动自检(版本 + 冒烟解析),失败仅告警不阻断启动(自动走内置路径);验证:make check 对应子集全绿;手工构造坏引擎场景,日志可见告警。

## 4. sqlfw 薄适配层(先红后绿)

- [x] 4.1 `check` / `check_schema_scope` / `check_reserved_tables` 共享单次解析结果,规则一比一移植;验证:2.1 对拍集全绿;单测断言单次解析(解析计数探针)。
- [x] 4.2 深嵌套受控:超深度上限 SQL 返回受控 ERROR verdict 而非 RecursionError;验证:深嵌套样本先红后绿。

## 5. datascope 与 agent validate 切换

- [x] 5.1 rewrite.py 复用同一解析结构,AST 注入 WHERE 行为与参数化不变;验证:datascope 域测试全绿,对拍集改写结果 diff 为空。
- [x] 5.2 agent validate 节点切引擎(纯解析无 IO 语义不变);验证:nl2sql-agent 相关测试子集全绿。

## 6. 降级与灰度

- [x] 6.1 请求级降级 fallback(引擎异常→内置路径)+ ENGINE_FALLBACK 审计与计数;验证:故障注入测试先红后绿。
- [x] 6.2 影子模式开关与不一致率观测;验证:影子开启时不一致仅记录、不影响 verdict;make check 全绿。

## 7. 契约与门禁

- [x] 7.1 import-linter 契约核查(agent 零凭证约束不放宽)+ `make check` 全量;验证:make check 全绿。
- [x] 7.2 手工冒烟:一次完整 NL2SQL 问答链路(流式),审计日志确认单次解析与活跃引擎记录;验证:冒烟证据贴 HANDOFF。
