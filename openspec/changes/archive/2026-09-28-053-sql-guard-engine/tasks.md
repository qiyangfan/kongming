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

## 8. 仓库形态合并(2026-09-12;独立仓 → packages/liannu,见 design 修订 6)

- [x] 8.1 引擎代码并入 `packages/liannu/`(源码+tests+冻结 fixture 原样迁移,包名定名连弩 liannu);验证:引擎 21 例在 api 环境全绿。
- [x] 8.2 两进程依赖切 uv path 依赖,import 改 `liannu`,fixture 导出脚本输出路径改仓内,vendor wheel 退役;验证:导出现算与冻结基线逐字节一致;`make check` 全量通过(api ruff/mypy 覆盖引擎代码,新增 liannu-check 目标)。

> 053 切主前置复核(2026-09-27,handoff 遗留项——本轮为复核,非实施):
> 1. 保留清单对象恒等断言:apps/api tests/test_reserved_single_source.py 与
>    apps/agent tests/test_sqlguard_single_source.py 均绿(api 侧含于
>    1024 passed 全量;agent 侧 1 passed 单独复核)。
> 2. 引擎自检含清单成员校验:sqlfw/guard.py _manifest_consistent 对
>    liannu.rules 五组常量逐成员比对(窄于权威即视为引擎不可用),067 前置
>    代码侧在位。
> 3. 运行日志(/tmp/km-agent-8001.log、/tmp/km-api-18000.log):ENGINE_FALLBACK
>    0 次、影子不一致 0 次;影子期自 2026-09-12 起算已逾一周(≥1 周条件满足)。
> 4. 版本 drift 备注:运行中进程报 version=0.1.2,仓内 packages/liannu 已
>    0.1.3(两进程 pyproject 均 path 依赖无 == 钉,无需锁文件同步;重启后
>    自然生效)。
> 结论:代码侧切主前置全部满足;剩余动作为运维侧——两进程 .env 置
> KM_SQLGUARD_MODE=primary(agent 前缀 KM_AGENT_LLM 同类:KM_AGENT_SQLGUARD_MODE)
> + 重启 + openspec archive。.env 涉凭证(读取受策略保护)且改运行为用户
> 部署环境、archive 涉 git 写操作(AGENTS.md:git 写操作需用户逐次明确
> 指令),本轮不改动、不臆测——标记【待确认-需用户授权】上报:授权后按
> 上述四步执行即可收口。

> 053 切主执行(2026-09-28 13:00,用户授权后):事实核查先行——apps/api/.env、
> apps/agent/.env 均不存在(HANDOFF §4 记载过时),settings env_file=".env"
> 为 cwd 相对,pydantic 环境变量优先——切主只需两进程 export。当时 api/agent
> 正以容器形态运行(api:18080/agent:18081,本轮冒烟栈),故用 compose
> override 注入 KM_SQLGUARD_MODE=primary / KM_AGENT_SQLGUARD_MODE=primary
> 重建(宿主机 8000 被上游 mock 占用,维持 18080/18081;redis 走 compose
> 网络内服务名,宿主机端口 6399 避让测试用 6380)。
> 执行结果(容器日志实证):
>   api:  "SQLGUARD 管线 mode=primary,活跃引擎=liannu version=0.1.3,启动自检通过"
>   agent:"SQLGUARD 管线 mode=primary,活跃引擎=liannu version=0.1.3,启动自检通过"
>   ENGINE_FALLBACK/引擎缺失/自检失败 = 0 次(切主后)。
> 版本漂移消解:运行进程由 0.1.2 → 0.1.3(复核项 4 关闭)。切主后经真实
> 流量验证:真实问答全链路(LLM 生成 SQL→primary 闸门判定→执行→结果)
> 多轮无降级、零 ENGINE_FALLBACK。
> 剩余:openspec archive 053-sql-guard-engine + git 提交范围——archive
> 涉 git 写操作,按 AGENTS.md 待用户逐次明确指令(本轮工作树另有 12 个
> change 的未提交改动,提交范围需用户裁定)。
