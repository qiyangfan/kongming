# Design: 043 生成纪律

## 决策

### D1 哨兵形态:裸 token `NO_RELEVANT_TABLE`

- 选择裸 token 而非 JSON/结构化输出:generate 现行输出契约是「只输出 SQL」
  (规则 7),单 token 检测最简且与 clean_sql 链路兼容。
- 检测在 clean_sql 之前对原始输出做:`raw.strip().strip("`").strip()` 后
  大小写折叠全等比较(含 `sql\n` 语言前缀剥离,覆盖 ```sql 标准包裹形态);
  哨兵出现在 SQL 中间(非全等)不触发,按普通 SQL 走既有防御(防火墙兜底)。
- 哨兵若被 LLM 拼进解释句(「我认为应输出 NO_RELEVANT_TABLE」),全等
  检测不命中→走 validate→防火墙 BLOCKED——退化为既有失败形态,可接受
  (非高频;prompt 已要求「不要解释」)。

### D2 短路拓扑:generate→validate 改条件边,复用 fail 终态

- `_after_generate(state)`:检测到哨兵短路产物 → `"fail"`;否则
  `"validate"`。build.py 的 `g.add_edge("generate", "validate")` 改
  `add_conditional_edges`(映射 `{"validate": "validate", "fail": END}`)。
- **不新增图节点**:短路产物 = `{generated_sql: "", error: <友好文案>,
  end_reason: "no_data"}`,经既有 `_after_selfheal` 无关路径直达 END;
  `end_reason` 进 AgentState 既有键(state.py 注释「终态分类(blocked 等)」
  同族)。路由铁律不破(无重试,不涉 selfheal 计数)。
- 流式通道零特殊:generate 与 fail 均经 stage_wrapper 包装,短路事件流
  与既有 blocked/retry_exhausted 终态同形态(chat 侧终态帧收集对 fail
  路径已验证)。

### D3 响应形态:契约零新增,FAILED+友好文案

- `_build_query_out` 对短路终态:execution=None → `verdict=None`、
  `success=False`、`error=<文案>`——与「LLM 故障未生成 SQL」既有形态
  同构(QueryOut.verdict 注释同步更新,把 no_data 列入 None 语义)。
- chat 侧 `_finalize` 三态映射零改动:非 success 且非 BLOCKED → FAILED;
  error 文案过输出护栏(既有路径)。
- 用户可见文案(定稿,错误横幅呈现):「当前数据源未找到与问题相关的
  数据表,可切换数据源或调整问法后重试」。**不带表名/不带内部术语
  (schema/检索/哨兵字样)**——UI 措辞范式(黑话禁入用户文案)。

### D4 prompt 改动:规则 10 显式豁免规则 7(关键冲突点)

规则 7「只输出 SQL,不要解释」与哨兵输出(非 SQL token)直接冲突,
规则 10 MUST 内含显式豁免句,否则纪律自相矛盾、模型输出不可预期:

> 10. 表范围纪律:FROM/JOIN/列名 MUST 只来自「可用表结构」段(表关系与
> 历史 SQL 仅供参考口径,不得引入其中不存在的表);若该段没有任何支撑
> 本问题的表,唯一例外于规则 7——只输出 `NO_RELEVANT_TABLE`,
> 不要解释、不要编造表名或列名。

`_EMPTY_HINT`(nodes.py 常量)同步重写,与哨兵机制联动:

> (未能检索到与问题相关的表结构;若问题所需数据不在可用范围内,只输出
> NO_RELEVANT_TABLE——严禁臆测表名或列名。)

**prompt 资产纪律落地**:prompts.py 文件头例外清单追加一条(2026-08-29,
043 规则 10+_EMPTY_HINT);test_prompts.py 既有断言全部保持通过(纯增
不断言),新增规则 10 关键句与哨兵 token 断言。

### D5 自愈喂回增强:nodes.generate 既有 `if error:` 段扩展

上次错误文本含 `does not exist`(大小写不敏感,PG UndefinedTable 报错
定式)时,extra 追加:「上次引用的表不在『可用表结构』清单中——核对清单;
若清单确无所需数据,只输出 NO_RELEVANT_TABLE。」其余错误形态喂回不变。
效果:同因编表第二回合即被引导到如实说明,不再 3 连败烧配额。

### D6 验证口径

- 端到端:default 租户重问事故同题 → 秒级返回友好文案,`sys_sql_audit`
  零新增执行记录(短路前零执行);sakila_dyn 源同题正常出数(纪律不误伤);
  bot/react 渠道回归(Non-goal 面,确认零影响)。
- 门禁:agent pytest+ruff+mypy、api 全量(web 零改动)、make check。

## 落点

| 文件 | 改动 |
|---|---|
| agent graph/prompts.py | 规则 10(含规则 7 豁免)+ 文件头例外清单登记 |
| agent graph/nodes.py | `_EMPTY_HINT` 重写;generate 哨兵检测+短路产物;喂回增强 |
| agent graph/build.py | generate→validate 条件边(`_after_generate`) |
| agent main.py | QueryOut.verdict 注释更新(None 语义含 no_data) |
| agent tests/test_prompts.py | 规则 10/哨兵/新空结构文案断言 |
| agent tests/test_nodes.py 等 | 哨兵短路/喂回增强/条件边用例 |
| contracts | 零漂移(QueryOut 无字段变化),`make contracts` 复核 |
