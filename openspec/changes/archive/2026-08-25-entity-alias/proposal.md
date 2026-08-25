# 037 entity-alias:实体别名(业务称谓)资产与表达对齐

## Why

用户以业务称谓提问(「用户表/订单表/商品表」),系统回答却回以物理
表名(`goods`/`tenant_default.goods` 甚至全限定名)——输入侧映射
(009 召回)已工作,输出侧表达对齐缺失。根因三层:摘要 prompt 无
称谓规则;表说明只有描述句无结构化称谓;summarize 上下文不含表
说明。本变更把**业务称谓立为可治理的语义资产**(一次提炼落库、
可手编纠正、处处一致——参照 LookML/dbt Semantic Layer 的 entity
alias 一等公民地位),而非每次回答由 LLM 现场提炼(不可治理、逐次
漂移、质量波动直接进用户视野)。

## What Changes

- **资产层**:rag.table_descriptions 加 `alias` 列(0024 迁移)——
  业务称谓短名(如「商品表」「影片分类表」)。来源三路优先级
  (009 覆盖优先级语义顺延):MANUAL_EDIT 手编(最高,润色不覆盖)
  > AI 提炼(009 润色管线同一次调用附带产出,零增量 LLM 成本;
  上游移植 prompt 按例外清单登记改动)> 规则兜底(NULL 时消费层
  回退表注释/物理名,存量表无需专项回填)。
- **消费层(表达对齐)**:rag 召回注入——schema_hint 表说明段带
  alias(embedding 文本拼入 alias,输入侧理解/召回双增强);agent
  summarize 上下文注入所涉表 alias 映射;SUMMARIZE_SYSTEM 加
  **称谓对齐规则**(称谓以用户用语与所给别名为准;禁止只写物理
  表名、禁止 schema 全限定名;首次提及可「业务名(物理名)」并注;
  不得编造别名——与 018 事实性规则同构;prompt 资产例外清单
  登记+全角脚本对拍)。
- **治理面**:/admin/rag 表说明编辑加 alias 字段(009 编辑面
  MANUAL_EDIT 顺延);生成类型经 make contracts。

## Capabilities

### New Capabilities

(无——既有能力的资产扩展与表达收口)

### Modified Capabilities

- `knowledge`:表说明润色管线产出实体别名(alias);管理面别名
  编辑;别名覆盖优先级(MANUAL_EDIT > AI 提炼 > 兜底)。
- `rag`:召回注入 alias(schema_hint 表说明段带别名;embedding
  文本拼入别名增强输入侧匹配)。
- `nl2sql-agent`:摘要/洞察的实体称谓表达规则(业务称谓优先,
  禁裸物理名/全限定名/编造)与 summarize 上下文别名注入。

## Non-goals

- 不改 SQL 生成侧表达(SQL 必须物理表名,本就正确);不改输入侧
  选表逻辑(009 召回已工作,alias 仅为增强非修补)。
- 不做图表轴标签/洞察卡标题的别名替换(摘要文本对齐后自然受益,
  前端不做文本替换)。
- 存量表不做专项 alias 回填批处理(reindex 自然刷新+消费层降级
  兜底)。
- 列级别名(列中文名已在 columns_comment,消费面随表别名后续)。

## Impact

- **代码**:0024 迁移(alias 列)+agent describer prompt 加 alias
  产出(例外清单登记)+api rag retrieve/schema_hint/embedding
  拼 alias+nodes.py summarize 注入+prompts.py SUMMARIZE_SYSTEM
  规则(例外清单+test_prompts 断言)+/admin/rag 编辑字段。
- **Schema**:0024(ADR-004 演进制,与 0008–0023 链式同批)。
- **契约**:rag 表说明响应/编辑请求纯增 alias;make contracts。
- **ADR 引用**:ADR-004(schema)/ADR-005(契约)。
- **评估红线**:016 evals smoke 复验(摘要形态变化不影响
  Execution Match;recipe 链路 prompt 改动按纪律冒烟先清同题
  query_history+km:ragctx 缓存)。
