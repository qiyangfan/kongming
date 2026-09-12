# 053-sql-guard-engine 设计

## Context

- 五次解析位置核实:
  1. `apps/api/kongming/sqlfw/firewall.py` — `check()`(只读根白名单 + 嵌套写/DDL 拦截 + SELECT INTO + 危险函数拒用)、`check_schema_scope()`(schema 白名单防跨租户)、`check_reserved_tables()`(保留表拒读 + CTE 遮蔽防护),各自独立 `sqlglot.parse_one`;
  2. `apps/api/kongming/datascope/rewrite.py` — data_scope 注入前再次 parse + AST 改写(立约 AST 非字符串拼接);
  3. `apps/agent/kongming_agent/graph/nodes.py` — validate 节点本地预检再 parse 一次(纯解析无 IO)。
- RecursionError 教训见 HANDOFF(sqlglot 深嵌套栈溢出)。
- 生态调研(2026-09 核实):sqlglotrs 已弃用(PyPI 标注 use sqlglotc);sqlglotc 为 Fivetran mypyc 编译版,解析约 5x,官方维护;sqlglot.lineage 存在但 CTE/歧义/输入改写等 issue 长尾;sqloxide(sqlparser-rs 现成 PyO3 绑定,月下载约 35 万)只暴露原始 AST、无校验无血缘;Rust 生态无 SQL 防火墙/血缘产品级 crate(仅 funcpp/sqllineage 等 2-star 幼儿期原型);sqlparser-rs 0.62 对真实 PG 语料存在约 28.7% 过宽误放行——该宽松性不构成本场景安全风险,见 Decisions 第 5 条。

## Goals / Non-Goals

**Goals**

- 一次执行链的 SQL 解析次数:5 → 1(引擎单次解析,全链路复用)。
- 深嵌套 SQL 从不可控 RecursionError 变为受控 ERROR verdict(引擎侧显式深度上限)。
- 行为等价切换:对拍不一致率 0(以现有 sqlfw/datascope 测试资产与 evals 为基线)。
- 引擎故障不放大:降级路径可用性不低于现行路径。

**Non-Goals**

- 血缘在 kongming 侧的任何 API 暴露(另立 change)。
- 单次 parse 速度对标 sqlglotc(无产品意义)。
- 多方言(维持 PG-only)。

## Decisions

1. **进程内 wheel,不做 sidecar。** *替代方案*:HTTP sidecar 服务——拒绝:引入新进程信任边界与内部令牌面,违反 ADR-002;运维面 +1;PyO3 进程内形态零新增边界。
2. **引擎独立新仓(km-sqlguard,暂定名),不放 kongming 子目录。** *替代方案*:kongming 内 Rust crate——拒绝:聚合工作区惯例是各项目独立成仓(kongming/muniu 先例);独立仓有自身 CI 与发布节奏,不绑死 kongming 独占使用。
3. **规则一比一移植 + 对拍门禁,不重新设计防火墙规则。** 语义逐条对照 sqlfw 现行实现;期望结果直接从现有测试与 evals 冻结生成,先红后绿(宪法 Article III)。
4. **降级为请求级 + 计数告警。** *替代方案*:进程级启动时锁定引擎——拒绝:引擎运行期异常应请求级回落而非整体回滚;PyO3 panic 以 Python 异常浮出,天然可请求级捕获。降级发生时审计日志记 ENGINE_FALLBACK,计数入 observability,阈值告警。
5. **sqlparser-rs 的语法宽松性不构成安全风险,不引入 pg_query。** 本管线安全性与语法严格性解耦:防火墙是 AST 白名单(拦「根节点非 SELECT」等),执行层有 PG 真解析兜底 + 连接级只读(`default_transaction_read_only`)。*替代方案*:pg_query.rs(真 PG parser)——拒绝:共享库形态破坏纯 wheel 分发;「放行了 PG 会拒的 SQL」会被 PG 拒绝,无放行风险,仅 verdict 形态差异(PG 报错 vs BLOCKED),可接受。
6. **深嵌套在引擎侧显式收口。** 解析深度上限可配置(默认 256,远超 LLM 生成 SQL 的实际形态),超限返回受控 ERROR 而非栈溢出;对拍集冻结深嵌套样本钉住该行为。
7. **影子模式先于切主。** 引擎与内置路径并行判定,仅记录不一致;不一致率连续观察归零后切主。灰度语义与 muniu 接入侧实践一致。

## Risks / Trade-offs

- [对拍长尾不一致(边角语法)] → 现有 sqlfw/datascope 测试 + evals 全量进对拍集;影子模式观察期;不一致即切主门禁失败,可无限期留在影子态,零风险敞口。
- [引擎仓维护成为长期负担(个人项目可持续性)] → 引擎仓范围刻意收窄(解析+校验+血缘,不做方言矩阵);fallback 永久存在使引擎故障可容忍,最坏情况退回内置路径,系统无损。
- [PyO3 wheel 平台矩阵(linux arm64/amd64、macOS)] → abi3 + maturin GitHub Actions 标准矩阵;CI 只需覆盖开发机与部署目标两平台。
- [双实现对拍的认知成本] → 对拍与降级逻辑内聚在 sqlfw 薄适配层,业务代码只见单一入口;切换状态记 HANDOFF。

## Migration Plan

1. 引擎仓 v0.1 就绪(独立演进,含对拍 fixture 导出)。
2. kongming 侧落地引擎调用 + 影子模式(默认影子),`make check` 全绿。
3. 影子观察期对拍不一致归零 → 配置切主(引擎为主,内置路径为 fallback)。
4. 观察 ≥1 周无 ENGINE_FALLBACK 异常 → 本 change 可归档;内置路径保留为 fallback 与对拍基线。

## Decisions(实施期修订,2026-09-12 落地实证后)

1. **引擎实现形态:Python 单遍引擎(sqlglot 同源 AST),替代 Rust/PyO3。**
   原决策 1/2/5 选 Rust(sqlparser-rs)动因是生态调研;落地时以验收点复核:
   本变更验收 = 消除重复解析 + 深嵌套受控 + 行为等价 + 降级可用,**全部与
   实现语言无关**(单次速度已明示为 Non-goal)。Python 引擎与内置路径共用
   sqlglot 同一 AST,规则一比一移植零「第二解析器」长尾偏差——原 design
   Risks 首条(对拍长尾不一致)直接消除;实证对拍 121 例零不一致。另本机
   无 Rust 工具链,免新增系统安装。PyO3/abi3 矩阵与 wheel 分发两个
   Open Questions 随 Rust 路线一并消除。引擎仍为独立仓(独立 CI/演进)、
   进程内纯计算(无网络/无凭证/无 DB),ADR-002 边界不变。
2. **深嵌套默认上限 256 → 32(可配置不变)。** 实测(sqlglot 30.17/Py3.13,
   递归限 1000):表达式嵌套 abs(abs(…)) 约 44 层抛 RecursionError(48 层必炸),
   子查询嵌套约 196 层(200 层必炸),深 AST 渲染同炸——「默认 256」被实测
   证伪(解析都过不去)。32 = 低于最苛刻形态实测临界并留安全边际,仍远超
   LLM 生成 SQL 实际形态;预扫描超限不进 sqlglot 直接受控拒绝,
   括号外病态形态由解析期 RecursionError 捕获兜底,异常永不逃逸。
3. **wheel 分发(原 Open Question):vendor wheel 快照替代 git 依赖。**
   kongming 仓根 `vendor/` 入库当前 wheel,两进程 `[tool.uv.sources]` path
   引用;升版 = 引擎仓 `uv build` 后换 wheel 提交并同步 `==` 版本声明
   (已演练两次:0.1.0→0.1.1 补 py.typed、0.1.1→0.1.2 修 dollar-quote
   词法——同名 wheel 换内容会被 uv 按路径缓存的元数据拒收,必须升版)。
   CI/离线 `uv sync --frozen` 均可解析,免发布面诉求与 git 直依一致且更强
   (离线可装)。
4. **shadow 模式对「内置路径会 RecursionError 的深嵌套 SQL」忠实保持旧行为
   (不拦不控)**——影子语义=行为与切换前逐字一致,含缺陷形态;受控拒绝
   仅在引擎模式(primary)生效,与 spec delta「GIVEN 管线运行于引擎模式」
   一致。切主后该形态自动获得受控 DEPTH_EXCEEDED。
5. **引擎审计归属**:spec「降级计入审计日志与可观测计数;活跃引擎可从
   审计日志判别」落为:结构化日志 WARNING 事件(启动活跃引擎行 +
   每次降级 ENGINE_FALLBACK 含 sql/check/异常类别 + 影子不一致含双方
   判定)+ 进程内计数器(fallback/mismatch/shadow);不改 `sys_sql_audit`
   表(提案硬约束:无 schema 变更),verdict 语义照旧落表。

## Open Questions

- wheel 分发:私有 PyPI 还是 git 直接依赖?倾向后者(个人项目免发布面),开工时定。
- abi3 目标版本随 kongming Python 3.13 锁定 cp313-abi3 还是宽 abi3,引擎仓开工时定。
