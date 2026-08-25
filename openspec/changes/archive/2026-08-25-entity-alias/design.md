# 037 entity-alias 设计

## Context

009 资产:rag.table_descriptions(table_comment 中文描述句/columns_
comment/source_type 四态含 MANUAL_EDIT/embedding/tsv)+LLM 润色管线
(agent describer.py 上游逐字移植 prompt)+/admin/rag 编辑面+召回
三路(向量/全文/历史 SQL)schema_hint 注入 generate。018:summarize
上下文=总行数+本地 stats+前 5 行(无表说明)。根因实证(2026-08-24
会话):摘要 LLM 照抄 SQL 物理名/全限定名;009 table_comment 是
描述句(「存储影片的分类信息」)非称谓(「影片分类表」)。

## Goals / Non-Goals

**Goals:** 业务称谓成为**可治理资产**(一次提炼落库+手编纠正+
处处一致);输出侧(摘要/洞察)称谓对齐用户语言;输入侧顺带增强
(alias 进召回与 schema_hint);零现场提炼(稳定性与成本双收)。
**Non-Goals:** 见 proposal(不动 SQL 表达/选表逻辑/前端替换/存量
回填批处理/列级别名)。

## Decisions

### D1 alias 是数据列,不是 prompt 技巧(核心决策)

称谓落 `rag.table_descriptions.alias`(0024 迁移,VARCHAR(64) NULL)。
**被否:纯 prompt 现场提炼**(此前会话的 A 方案)——同一批表被问
N 次就现场提炼 N 次,称谓逐次漂移(今天「订单表」明天「订单信息
表」),用户无法纠正,LLM 起名质量波动直接进用户视野;称谓是
**语义资产**(参照 LookML/dbt entity alias 一等公民地位),资产的
归宿是数据列+治理面,不是 prompt 里的祈祷。
**被否:靠 table_comment 描述句给 LLM 自由提炼**——描述句→称谓
的提炼正是要消除的现场计算。

### D2 三路来源与覆盖优先级(009 语义顺延)

MANUAL_EDIT(管理面手编,润色永不覆盖——009 既有覆盖优先级
条款直接覆盖 alias 列)> AI 提炼(009 润色管线同一次 LLM 调用
附带产出 alias 字段,**零增量 LLM 成本**;describer prompt 为上游
逐字移植资产,本变更按 prompts.py 例外清单同款登记——文件头
注记+describer 单测断言钉住)> 兜底(alias NULL 时消费层回退
table_comment/物理名,存量表零专项回填,reindex 自然刷新)。

### D3 消费注入双侧

- **api rag 侧**:retrieve 的 schema_hint 表说明段拼装带 alias
  (形态 `表名(alias):表说明`);build_embedding_text 拼入 alias
  (召回输入侧增强——用户说「商品表」命中 alias 向量/全文)。
- **agent 侧**:nodes.py summarize 上下文补「所涉表 alias 映射」
  (state.schema_hint 已在 state,提取/直拼该段——018 上下文
  [总行数+stats+前5行] 顺延);SUMMARIZE_SYSTEM 加称谓对齐规则
  (见 proposal;prompt 资产例外清单+全角脚本对拍+test_prompts
  断言)。generate 侧零改动(schema_hint 已带 alias,自然受益)。

### D4 治理面

/admin/rag 表说明编辑对话框加 alias 输入(009 编辑面 MANUAL_EDIT
顺延);响应/请求纯增字段(make contracts 生成)。

## Risks / Trade-offs

- [alias 提炼质量参差(如「数据表」这类废名)] → 提炼 prompt 约束
  「业务称谓短名 ≤10 字,禁『数据/信息/表』堆砌」+消费层规则要求
  「用户用语优先于 alias」——用户已给称谓时 LLM 不被 alias 带偏;
  治理面手编兜底。
- [SUMMARIZE 规则与 018 事实性规则张力(称谓美化 vs 不外推)] →
  规则显式「不得编造别名,以所给别名/用户用语为准」,与 018
  「不得外推」同构互锁;test_prompts 断言双规则共存。
- [存量 alias NULL 期体验参差] → 消费层降级链(alias→table_
  comment→物理名)显式;reindex 一键刷新(既有入口)。

## Migration Plan

0024 迁移单对象(rag.table_descriptions ADD alias VARCHAR(64)
NULL;零存量改写);downgrade 删列。滚动兼容:旧代码读 alias=NULL
走降级链,新代码逐步生效;与 0008–0023 链式同批按序提交。
