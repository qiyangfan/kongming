# 051-entity-match-sentinel 提案:表级哨兵的实体核对判据

## Why

立案案例实证:default 数据源(goods 演示表)上问「每个门店库存中各影片分类的
库存数量」,generate 未触发 NO_RELEVANT_TABLE 哨兵,而是硬凑出
`SELECT create_by AS category, COUNT(*) FROM goods`——把创建人当影片分类、
把任意计数当库存。根因两层:① 043 的表级哨兵判据「没有支撑本问题的表」
从未被操作化——模型没编造表名、也没缺列,规则 10 字面全合规,"支撑"与否
全凭模型自觉,实体领域错配(问题问门店/影片,表只有商品)在判据上无锚点;
② 硬凑 SQL 执行成功后经 query_history 回写成为后续同题生成的 few-shot
参考(041「回写自我繁殖」同款疾病),坏答案在自我强化。现有反例只覆盖
「缺列硬凑」(046),不覆盖「实体错配硬凑」。

## What Changes

- **规则 10 实体核对判据(prompt)**:选表前先做实体核对——问题核心业务
  实体(门店/影片/客户/订单等具体名词)必须能在可用表结构的表说明或列语义
  中找到对应,方为「支撑本问题的表」;「任何表都能 COUNT/分组」不构成相关,
  把问题里的量词读成对任意表的计数、把任意文本列读成问题里的分类维度,
  均为硬凑。
- **对位反例点名(prompt)**:把本次实证失败案例原样钉进反例清单
  (046 已证方法论:抽象条款不稳定,点名+对位反例才 3/3 稳定)。
- **负例评估回归(eval)**:新增 `expect_no_data` 用例形态(200+success=False
  +verdict=NO_DATA+SQL 空=零执行,与 expect_reject 的 HTTP 拒绝相区分);
  新建 default.yaml 负例集(本案例+一个泛化变体),其中一题进 smoke 第一道闸。
- **污染清理(runbook)**:运行评估前清 rag.query_history 同题沉淀与 ragctx
  缓存(README 已载明的既有口径,本次对污染行执行)。

## Non-goals

- **不做确定性拒答门禁**(召回分数阈值/词面重叠除外判定):召回相似度未
  校准,阈值拒答会产生不可恢复的假 NO_DATA(可用性伤害),且与 understand
  节点的 NLU 职责重复——判据落点保持在 generate 提示词,以评估负例守护。
- 不改 query_history 回写策略(009 自动回写语义;点赞驱动回写属独立演进)。
- 不改 understand 节点(实体抽取前置属重复 NLU,不采)。
- 不做 043 短路机制改动(机制完好,失守在判据不在管道)。

## Capabilities

### Modified Capabilities

- `nl2sql-agent`:「表范围纪律与如实说明短路」Requirement 补表级判据
  操作化(实体核对)+实体错配场景。
- `eval`:新增「负例评估(如实弃答)」Requirement——expect_no_data 用例
  形态与断言口径。

## Impact

- apps/agent:`graph/prompts.py`(规则 10 判据子句+反例一条)。
- apps/api/evals:`default.yaml`(负例 2 题)、`test_eval.py`(expect_no_data
  分支)、`README.md`(用例形态与文件表)。
- 无 schema/契约变化;dev 库执行一次污染行清理(数据删除,范围以 question
  精确匹配,评估重复性口径的既有动作)。
