# 设计：拆分转义并显式区分 SQL 模糊模式

## Context

当前 `like_pattern` 同时完成两个动作：把用户输入中的 LIKE 元字符转为字面量，以及添加两侧通配符以表达包含匹配。chat、datasource、store、system 仓储均使用该模式，SQL 条件本身和 `ESCAPE '\\'` 声明仍由仓储负责。

当前这些查询使用 `ILIKE '%...%'`。baseline 中没有针对这些搜索字段的 B-tree pattern-ops 或 trigram 索引。PostgreSQL 文档说明，B-tree 的模式匹配索引条件是常量且左侧锚定的 `LIKE` 模式；对 `ILIKE` 的 B-tree 支持更受限。`pg_trgm` 则支持 `LIKE` 与 `ILIKE`，不要求模式左锚定。因此单独改成前缀模式或新增模式构造器，不足以证明当前查询获得索引收益。

## Goals / Non-goals

**Goals**

- 将 LIKE 元字符转义和 SQL 包含模式拼装分配到独立工具模块。
- 让 SQL 工具通过单向依赖复用转义工具，并显式提供前缀、后缀和包含三种模式，不在转义工具中加入 `%` 包装或 SQL 查询构造。
- 保持所有现有调用点的模式字符串和搜索结果不变。
- 为每种模式记录索引适用条件；实际索引选择必须基于查询执行计划与代表性数据验证。

**Non-goals**

- 不修改任何现有搜索域的匹配策略；现有调用仍为包含匹配。
- 不在没有代表性查询计划证据时添加搜索索引；索引优化须由具体查询需求、列类型/排序规则及执行计划共同确定。
- 不实现泛化到所有语法的单一转义函数。

## Decisions

### D1：按职责建立两个模块及单向依赖

- `escape_util.py` 提供 `escape_like_literal(value: str) -> str`，只负责将反斜杠、百分号和下划线按 LIKE 规则转义，不添加通配符。
- `sql_util.py` 提供 `prefix_like_pattern(value: str) -> str`、`suffix_like_pattern(value: str) -> str` 和 `contains_like_pattern(value: str) -> str`，均调用 `escape_like_literal` 后分别添加右侧、左侧或两侧 `%`。
- 依赖方向为 `sql_util → escape_util`。仓储继续声明 SQL 运算符、`ESCAPE` 子句和绑定参数。
- 不提供跨不同语法的泛型 `escape()`：转义字符、边界和规则依赖目标语法，新增其他上下文时应增加有明确语义的转义函数。

该边界保留 `contains_like_pattern` 的具体语义，但将它放入 SQL 工具域；不会把它放进转义模块，也不会把所有工具收进一个通用 `tools` 模块。

### D2：显式区分匹配模式，但保留现有调用语义

三个模式各有明确输出：前缀 `escaped + "%"`、后缀 `"%" + escaped`、包含 `"%" + escaped + "%"`。仓储必须根据具体搜索语义选择模式；现有四个仓储域都继续选择包含模式，因此当前结果不变。

匹配模式与索引能力分开判断。前缀模式在左锚定、模式常量且排序规则/操作符类适配时可使用 B-tree；当前查询为 `ILIKE`，仅把模式改成 `keyword%` 不能保证 B-tree 命中。后缀和包含模式不适合普通 B-tree，可评估 `pg_trgm`；短到无法提取有效 trigram 的模式也可能退化为扫描。任何索引变更都需针对真实字段和代表性参数比较 `EXPLAIN (ANALYZE, BUFFERS)`，确认收益后再纳入迁移。

### D3：不变更行为契约

函数抽取和仓储导入迁移后，现有包含函数对同一输入返回与旧 `like_pattern` 相同的模式，包括反斜杠、`%`、`_` 以及空字符串。仓储 SQL、搜索过滤条件、排序、权限和参数绑定均不调整，因此无需 spec delta 或 OpenAPI 更新。只有经过查询计划验证并决定添加索引时，才增加对应数据库迁移。

### 搜索索引评估

现有查询的索引候选按实际 SQL 运算符确定。各字段未显式指定 `COLLATE`，使用列/数据库默认排序规则；本次可访问的 `kongming_test` 为 `en_US.utf8`。搜索大小写语义由 `ILIKE` 决定，新增模式构造器本身不选择 `LIKE` 或 `ILIKE`。

| 仓储查询 | 字段 | 当前模式与大小写语义 | 候选索引 |
|---|---|---|---|
| chat | `chat_session.title`、`chat_message.question` | 包含，`ILIKE`，不区分大小写 | `pg_trgm` 的 GIN；B-tree 不适用于当前左侧未锚定的模式 |
| datasource | `sys_datasource.datasource_key`、`name` | 包含，`ILIKE`，不区分大小写 | `pg_trgm` 的 GIN |
| system | `sys_user.username`、`nickname` | 包含，`ILIKE`，不区分大小写 | `pg_trgm` 的 GIN |
| store | `sys_bot_definition.bot_key`、版本快照的 `name` 和 `system_prompt` | 包含，`ILIKE`，不区分大小写 | `pg_trgm` 的 GIN；快照字段需针对表达式建索引 |

在隔离事务的临时表上生成 200,000 条代表性文本，分别执行前缀 `ILIKE 'needle%'`、后缀 `ILIKE '%needle'`、包含 `ILIKE '%needle%'` 和短词 `ILIKE '%ab%'`，对比无索引、`text_pattern_ops` B-tree 与 `gin_trgm_ops` GIN 的 `EXPLAIN (ANALYZE, BUFFERS)`。事务最后回滚，未改动测试库对象。无索引时四种查询均为顺序扫描；B-tree 对大小写敏感的 `LIKE 'needle%'` 使用了 Bitmap Index Scan，但对现有 `ILIKE 'needle%'` 仍是顺序扫描，不能在保持语义的前提下替代当前索引候选。加 GIN 后，三个含足够 trigram 的 `ILIKE` 模式使用 Bitmap Index Scan，模式重检后分别返回 2,000、2,000、6,000 行；两字符 `%ab%` 仍走顺序扫描，返回 2,000 行。索引前后行数相同。

该探针证明 `pg_trgm` 能为足够长的三种 `ILIKE` 模式提供候选路径，也验证短词会退化扫描且不丢结果。合成数据的命中行分散在全部堆页，GIN 执行计划仍读取全部 1,279 个本地堆页；一次临时表探针无法证明各业务表的实际读收益，也没有覆盖额外索引的存储和写入成本。因此本 change 不加生产索引迁移。若后续要建索引，须先用对应业务表的数据分布和完整仓储查询复核，并按 [ADR-004：Schema 基线纪律](../../../../docs/adr/004-schema-baseline.md) 新增 Alembic 迁移。

## Risks / Trade-offs

- 两个新模块初始各自只有一个职责；SQL 工具会有三种模式构造函数，但共享同一转义实现，避免重复转义逻辑。
- 误迁移调用点可能破坏字面量搜索；应保留转义单测与现有仓储集成测试。
- 新增模式构造函数本身不产生索引收益；只有具体查询采用对应模式且执行计划证明索引有效，才能称为性能优化。

## References

- [PostgreSQL 18：Index Types](https://www.postgresql.org/docs/18/indexes-types.html)（B-tree 对左锚定 `LIKE` 与 `ILIKE` 的条件）
- [PostgreSQL 18：pg_trgm](https://www.postgresql.org/docs/18/pgtrgm.html)（trigram 索引支持 `LIKE`/`ILIKE` 且不要求左锚定）
- [PostgreSQL 18：Operator Classes](https://www.postgresql.org/docs/18/indexes-opclass.html)（pattern-ops 对排序规则的适用条件）
- [ADR-004：Schema 基线纪律](../../../../docs/adr/004-schema-baseline.md)（若执行计划证明需要索引，新增索引须通过 Alembic 迁移）
