# liannu · 连弩

> 损益**连弩**,**木牛**流马,皆出其意。——《三国志·蜀书·诸葛亮传》
> 与木牛同出处的孔明发明:守城武备(SQL 安全防火墙);「一弩十矢俱发」——单遍解析,一次产出防火墙判定 / 作用域校验 / 保留表 / 行级改写多路结果。

Kongming 的 SQL 安全引擎:一次解析同时产出防火墙判定、schema 作用域校验、
保留表拒读与行级改写所需结构,消除执行链路上的重复解析;以显式深度上限把
深嵌套 SQL 从不可控 `RecursionError` 变为受控 `DEPTH_EXCEEDED` 拒绝。

**纯进程内计算库**:仅依赖 sqlglot(PG 方言),无网络、无凭证、无 DB 访问。
判定规则与消费方 kongming 的内置实现(sqlfw/datascope)一比一移植,
行为等价以冻结的对拍 fixture 门禁保证(`tests/fixtures/kongming_baseline.json`)。

## API 一览

```python
from liannu import GuardArtifact, parse, self_check, DEFAULT_MAX_DEPTH

# 单遍解析(深度预扫描 → sqlglot 单次 parse)
art = parse("SELECT * FROM tenant_a.goods WHERE id = 1")

art.check_firewall()  # Verdict(passed, reason) 只读根白名单/写节点/危险函数
art.check_schema_scope({"tenant_a", "public"})  # 显式 schema 限定白名单
art.check_reserved_tables()  # 保留表/保留 schema/CTE 遮蔽
art.main_table_ref()  # (表名, 显式 schema|None) | None(行级改写结构)
art.apply_scope_filter("USER_EQ", "create_by", ["u-1"])
# → (改写后 SQL, {"km_ds_0": "u-1"}):谓词 AND 进最外层 WHERE,参数化绑定,
#   占位符渲 :name 风格(SQLAlchemy text() 参数)
```

`Verdict` 为不可变值对象;reason 字符串与 kongming 内置实现逐字一致
(`PARSE_ERROR:` / `EMPTY_STATEMENT` / `BLOCKED_ROOT:` / `BLOCKED_INTO` /
`BLOCKED_NODE:` / `FORBIDDEN_FUNC:` / `CROSS_SCHEMA:` / `RESERVED_TABLE:`),
消费方影子对拍以此为键。唯一新增 reason 是 `DEPTH_EXCEEDED:`(受控拒绝,
内置路径无此形态——内置路径深嵌套直接 `RecursionError`)。

## 深度上限(实测记录)

sqlglot 解析是递归下降,深嵌套会抛 `RecursionError`(不在 SqlglotError 族)。
本机实测(Python 3.13.13 / sqlglot 30.17.0,默认递归限 1000):

| 形态 | 实测临界 |
|---|---|
| 表达式嵌套 `abs(abs(…))` | 约 44 层爆栈(48 层必炸) |
| 子查询嵌套 `FROM (…)` | 约 196 层爆栈(200 层必炸) |
| 深嵌套 AST 渲染 `.sql()` | 同样递归,深 AST 渲染也炸 |

默认上限取 **32**(DEFAULT_MAX_DEPTH):低于最苛刻形态的实测临界并留安全边际,
又远超 LLM 生成 SQL 的实际形态(通常 ≤10 层)。可配置
(`parse(sql, max_depth=N)`);超限**不进 sqlglot** 直接受控拒绝。
预扫描漏掉的括号外病态形态(如超长一元链)由 parse 期 `RecursionError`
捕获兜底,同样收敛为 `DEPTH_EXCEEDED`,异常永不逃逸。

## 对拍 fixture

`tests/fixtures/kongming_baseline.json` 由消费方 kongming 侧导出脚本
(基于其内置实现与既有测试资产/evals gold)冻结生成,重复运行 diff 为空。
`test_baseline_replay.py` 将引擎 API 回放结果与冻结期望逐例比对——
规则移植漂移即测试红。深嵌套样本在 fixture 的 `depth_cases` 节,
仅钉引擎受控行为(内置路径无对应形态)。

## 位置与门禁

连弩为 kongming monorepo 共享包(`packages/liannu`),apps/api 与 apps/agent
以 uv path 依赖引入;lint/type 随 api 门禁覆盖,对拍基线回放走根 `make check`
的 `liannu-check` 目标。引擎为纯计算库,不触 DB 驱动,满足 kongming agent 面
import-linter 契约(禁 asyncpg/sqlalchemy/psycopg/redis)。

升版:改本包 `pyproject.toml` 版本号,同步两进程 `pyproject.toml` 的
`==` 版本声明(`make check` 全绿后提交)。
